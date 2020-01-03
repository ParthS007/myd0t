#!/usr/bin/env python3

import grp
import os
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
from argparse import ArgumentParser, ArgumentTypeError
from pathlib import Path

from colorama_ansi import Fore, clear_line

MYD0T = f'{Fore.LIGHTWHITE}myd0t{Fore.RESET}'
DISTROS = {
    'gentoo': {
        'install': ['emerge', '-avn'],
        'packages': {
            'git': 'dev-vcs/git',
            'zsh': 'app-shells/zsh',
            'tmux': 'app-misc/tmux',
            'vim': 'app-editors/vim',
        },
        'set_editor': ['eselect', 'editor', 'set', 'vi'],
        'vimrc': '/etc/vim/vimrc.local',
    },
    'arch': {
        'install': ['pacman', '-S'],
        'packages': {'git': 'git', 'zsh': 'zsh', 'tmux': 'tmux', 'vim': 'vim'},
        'set_editor': None,
        'vimrc': '/etc/vimrc',
    },
    'fedora': {
        'install': ['dnf', 'install'],
        'packages': {'git': 'git', 'zsh': 'zsh', 'tmux': 'tmux', 'vim': 'vim'},
        'set_editor': None,
        'vimrc': '/etc/vimrc',
    },
    'centos': {
        'install': ['yum', 'install'],
        'packages': {'git': 'git', 'zsh': 'zsh', 'tmux': 'tmux', 'vim': 'vim'},
        'set_editor': None,
        'vimrc': '/etc/vimrc',
    },
    'ubuntu': {
        'install': ['apt', 'install'],
        'packages': {'git': 'git', 'zsh': 'zsh', 'tmux': 'tmux', 'vim': 'vim'},
        'set_editor': ['update-alternatives', '--set', 'editor', '/usr/bin/vim.basic'],
        'vimrc': '/etc/vim/vimrc.local',
    },
    'debian': {
        'install': ['apt', 'install'],
        'packages': {'git': 'git', 'zsh': 'zsh', 'tmux': 'tmux', 'vim': 'vim'},
        'set_editor': ['update-alternatives', '--set', 'editor', '/usr/bin/vim.basic'],
        'vimrc': '/etc/vim/vimrc.local',
    },
}


def guess_distro():
    if os.path.exists('/etc/os-release'):
        return subprocess.check_output(
            ['sh', '-c', '. /etc/os-release; echo -n $ID']
        ).decode()
    elif os.path.exists('/etc/arch-release'):
        # at least the docker image doesn't have /etc/os-release
        return 'arch'


def check_programs(distro, programs):
    missing = {p for p in programs if not shutil.which(p)}
    colors = {p: Fore.LIGHTRED if p in missing else Fore.LIGHTGREEN for p in programs}
    result = {f'{colors[p]}{p}{Fore.RESET}' for p in programs}
    print(f'required packages: {", ".join(sorted(result))}')
    if not missing:
        return True
    try:
        install_packages(distro, missing)
    except KeyboardInterrupt:
        clear_line()
        print(f'\r{Fore.LIGHTRED}package installation aborted{Fore.RESET}')
        return False
    else:
        return check_programs(distro, programs)


def install_packages(distro, packages):
    try:
        distro_data = DISTROS[distro]
    except KeyError:
        print('please install the following packages:\n')
        for p in packages:
            print(f' - {p}')
        print()
        input('press ENTER once you are done\n')
        return
    distro_packages = [distro_data['packages'][x] for x in packages]
    args = [*distro_data['install'], *distro_packages]
    cmdline = ' '.join(map(shlex.quote, args))
    if os.geteuid() != 0:
        print('run the following command as root to install missing packages:\n')
        print(f'    {Fore.LIGHTWHITE}{cmdline}{Fore.RESET}')
        print()
        input('press ENTER once you are done\n')
    else:
        print(
            f'i will run {Fore.LIGHTWHITE}{cmdline}{Fore.RESET} to install missing packages'
        )
        input('press ENTER to continue\n')
        try:
            subprocess.check_call(args)
        except subprocess.CalledProcessError:
            print('non-zero exit code; installation likely failed')


def check_root(user_install):
    print('root? ', end='')
    if user_install:
        print(f'{Fore.YELLOW}not needed{Fore.RESET}')
        return True
    if os.geteuid() != 0:
        print(f'{Fore.LIGHTRED}FAILED{Fore.RESET}')
        print('re-run with `sudo` or use `--user` to install everything locally')
        print('if this is your machine use `sudo`; otherwise use `--user`')
        return False
    print(f'{Fore.LIGHTGREEN}OK{Fore.RESET}')
    return True


def is_tmux_2():
    try:
        output = subprocess.check_output(['tmux', '-V']).decode().strip()
    except (OSError, subprocess.CalledProcessError):
        print('FAILED')
        return None
    return re.match(r'tmux (\d+)', output).group(1) == '2'


def install_tmux(base_dir, target_dir, target_bin_path, user_install):
    print('- tmux')
    target_dir.mkdir(exist_ok=True)
    source_file = 'tmux.conf' if not is_tmux_2() else 'tmux-legacy.conf'
    target_path = target_dir / 'tmux.conf'
    tmux_config_path = Path(
        '~/.tmux.conf' if user_install else '/etc/tmux.conf'
    ).expanduser()
    # copy to myd0t config dir
    shutil.copy(base_dir / source_file, target_path)
    # delete existing config (hopefully not something the user still needed)
    if tmux_config_path.exists() or tmux_config_path.is_symlink():
        tmux_config_path.unlink()
    # link to the config file inside the myd0t config dir
    tmux_config_path.symlink_to(target_path)
    # create an override file in case the user wants to add more stuff
    custom_config_path = target_dir / 'tmux.user.conf'
    if not custom_config_path.exists():
        custom_config_path.touch()
    smartsplit_path = target_bin_path / 'tmux-smartsplit'
    replace_placeholders(
        target_path, custom_config_path=custom_config_path, smartsplit=smartsplit_path,
    )


def install_zsh(base_dir, target_dir, user_install):
    print('- zsh')
    target_dir.mkdir(exist_ok=True)
    tm_config_path = target_dir / 'config-tm'
    if user_install:
        zshrc_path = Path('~/.zshrc').expanduser()
        zshenv_path = None
        try:
            if zshrc_path.exists() and not zshrc_path.is_symlink():
                input(
                    '~/.zshrc will be overwritten. Press ENTER to confirm, CTRL+C to abort\n'
                )
        except KeyboardInterrupt:
            print('\rzsh setup aborted')
            return
    elif Path('/etc/zsh').exists():
        zshrc_path = Path('/etc/zsh/zshrc')
        zshenv_path = Path('/etc/zsh/zshenv')
    else:
        zshrc_path = Path('/etc/zshrc')
        zshenv_path = Path('/etc/zshenv')
    # delete previously-copied config since copytree fails if target dirs exist
    if tm_config_path.exists():
        shutil.rmtree(tm_config_path)
    # delete old config (hopefully not something the user still needed)
    if zshrc_path.exists() or zshrc_path.is_symlink():
        zshrc_path.unlink()
    if zshenv_path and (zshenv_path.exists() or zshenv_path.is_symlink()):
        zshenv_path.unlink()
    # copy to myd0t config dir
    shutil.copytree(
        base_dir / 'config-tm',
        tm_config_path,
        ignore=lambda p, n: {'.git', '.gitignore', '.gitmodules', 'README.md'},
    )
    # link to our config files
    zshrc_path.symlink_to(target_dir / 'zshrc')
    if zshenv_path:
        zshenv_path.symlink_to(target_dir / 'zshenv')
    # create override files in case the user wants to add more stuff
    custom_zshrc_path = target_dir / 'zshrc.user'
    if not custom_zshrc_path.exists():
        custom_zshrc_path.touch()
    if zshenv_path:
        custom_zshenv_path = target_dir / 'zshenv.user'
        if not custom_zshenv_path.exists():
            custom_zshenv_path.touch()
    # copy the configs our symlinks point to
    replace_placeholders(
        target_dir / 'zshrc',
        base_dir / 'zshrc',
        zshrc=tm_config_path / '.zshrc',
        editor_env=target_dir.parent / 'vim' / 'editor-env.sh',
        custom_zshrc=custom_zshrc_path,
    )
    if zshenv_path:
        replace_placeholders(
            target_dir / 'zshenv',
            base_dir / 'zshenv',
            zshenv=tm_config_path / '.zshenv',
            custom_zshenv=custom_zshenv_path,
        )


def install_git(base_dir, target_dir, target_bin_path, user_install):
    print('- git')
    target_dir.mkdir(exist_ok=True)
    target_file_path = target_dir / 'gitconfig'
    git_config_arg = '--global' if user_install else '--system'
    smartless_path = target_bin_path / 'smartless'
    replace_placeholders(
        target_file_path, base_dir / 'gitconfig', smartless=smartless_path
    )
    subprocess.check_call(
        [
            'git',
            'config',
            git_config_arg,
            '--replace-all',
            'include.path',
            str(target_file_path),
            f'^{re.escape(str(target_file_path))}$',
        ]
    )


def replace_placeholders(file: Path, infile: Path = None, **placeholders):
    data = (infile or file).read_text()
    for name, value in placeholders.items():
        data = data.replace(f'@@{name}@@', str(value))
    if file is not None:
        file.write_text(data)
    return data


def install_editor(base_dir, target_dir, user_install, distro):
    print('- editor [vim, what else]')
    target_dir.mkdir(exist_ok=True)
    target_file_path = target_dir / 'vimrc'

    # copy vim config
    shutil.copy(base_dir / 'vimrc', target_file_path)

    try:
        distro_data = DISTROS[distro]
    except KeyError:
        distro_data = None
        vimrc_msg = ''
        if not user_install:
            vimrc_msg = ' and load {target_file_path}'
        print(
            f'{Fore.YELLOW}unknown distro; you need to set the default '
            'editor{vimrcmsg} manually{Fore.RESET}'
        )
    else:
        # set default editor in environment
        env_file_path = target_dir / 'editor-env.sh'
        shutil.copy(base_dir / 'editor-env.sh', env_file_path)
        # in case of a user install we rely on the shell config
        if not user_install:
            profile_d_path = Path('/etc/profile.d/myd0t-editor.sh')
            if profile_d_path.exists() or profile_d_path.is_symlink():
                profile_d_path.unlink()
            profile_d_path.symlink_to(env_file_path)
            # run custom command to set the editor
            set_editor = distro_data['set_editor']
            if set_editor:
                subprocess.check_call(set_editor, stdout=subprocess.DEVNULL)

    vimrc_path = None
    if user_install:
        vimrc_path = Path('~/.vimrc').expanduser()
    elif distro_data:
        vimrc_path = Path(distro_data['vimrc'])

    colors_path = Path(
        '~/.vim/colors' if user_install else '/usr/share/vim/vimfiles/colors'
    ).expanduser()
    colors_path.mkdir(parents=True, exist_ok=True)
    shutil.copy(base_dir / 'darcula_myd0t.vim', colors_path / 'darcula_myd0t.vim')

    if vimrc_path is not None:
        loader = replace_placeholders(None, base_dir / 'loader', vimrc=target_file_path)
        if not vimrc_path.exists() or not vimrc_path.read_text().strip():
            # vimrc doesn't exist or is empty - just use ours
            vimrc_path.write_text(loader)
        else:
            # append our loader to the existing file
            old_vimrc = vimrc_path.read_text().rstrip()
            if loader.strip() in old_vimrc:
                pass
            elif str(target_file_path) in old_vimrc:
                print(f'  {vimrc_path.name} has already been patched (but modified)')
            else:
                vimrc_path.write_text(f'{old_vimrc}\n\n{loader}'.strip())


def install_dconf(base_dir, user_install, user):
    print('- gnome terminal')
    if not shutil.which('gnome-terminal'):
        print('gnome-terminal not installed; skipping terminal config')
        return
    if not shutil.which('dconf'):
        print('dconf not installed; skipping terminal config')
        return

    sudo = []
    if not user_install:
        # during a global install we're root so we need to switch to the user
        sudo = ['sudo', '-E', '-u', user]

    terminal_conf = (base_dir / 'gnome-terminal.ini').read_bytes()
    try:
        subprocess.run(
            [*sudo, 'dconf', 'load', '/org/gnome/terminal/'], input=terminal_conf
        )
    except subprocess.CalledProcessError:
        print('non-zero exit code; loading terminal config likely failed')


def do_update_shell(user):
    rec = pwd.getpwnam(user)
    if rec.pw_shell == '/bin/zsh':
        return

    usermod_args = ['usermod', '-s', '/bin/zsh', user]
    usermod_cmd = ' '.join(map(shlex.quote, usermod_args))
    if os.geteuid() == 0:
        print(f'updating shell for {user}')
        subprocess.run(usermod_args)
        return

    has_chsh = shutil.which('chsh') is not None
    if has_chsh and user == pwd.getpwuid(os.geteuid()).pw_name:
        print(
            f'updating shell for {user} using chsh - you may need to enter your password'
        )
        try:
            subprocess.run(['chsh', '-s', '/bin/zsh'], check=True)
        except subprocess.CalledProcessError:
            pass
        else:
            return

    print(
        f'could not update shell; run {Fore.LIGHTWHITE}{usermod_cmd}{Fore.RESET} as root'
    )


def update_shell(primary_user):
    print(f'- default shell')
    user = pwd.getpwuid(os.geteuid()).pw_name
    do_update_shell(user)
    if primary_user:
        do_update_shell(primary_user)


def get_install_mode():
    uid = os.geteuid()
    is_root = uid == 0
    user = pwd.getpwuid(uid).pw_name
    groups = {grp.getgrgid(g).gr_name for g in os.getgroups()}
    msg = f'''
        Welcome, {Fore.CYAN}{user}{Fore.RESET}!

        If this is {Fore.GREEN}your system{Fore.RESET}, it is recommended to install {MYD0T} globally.
        This allows you to have the same nice environment when switching to
        a root shell or other users.

        If this is a {Fore.YELLOW}shared system{Fore.RESET} where you might not even have root access,
        you need to install {MYD0T} locally, just for your own user.
    '''
    print(textwrap.dedent(msg).strip())
    print()
    if is_root:
        msg = (
            f'Recommendation: install {Fore.GREEN}globally{Fore.RESET} '
            f'(you are {Fore.LIGHTRED}root{Fore.RESET})'
        )
        system = True
    elif groups & {'wheel', 'sudo', 'admin'}:
        msg = (
            f'Recommendation: install {Fore.GREEN}globally{Fore.RESET} '
            f'(you most likely have {Fore.LIGHTRED}sudo access{Fore.RESET})'
        )
        system = True
    else:
        msg = (
            f'Recommendation: install {Fore.YELLOW}locally{Fore.RESET} '
            '(unless you have sudo access)'
        )
        system = False
    print(msg)
    print()
    if system:
        if not confirm(
            f'Continue with {Fore.GREEN}global{Fore.RESET} install?', default=True
        ):
            if confirm(
                f'Install {Fore.YELLOW}locally{Fore.RESET} instead?', default=True
            ):
                system = False
            else:
                sys.exit(1)
    else:
        if not confirm(
            f'Continue with {Fore.YELLOW}local{Fore.RESET} install?', default=True
        ):
            if confirm(
                f'Install {Fore.GREEN}globally{Fore.RESET} instead?', default=True
            ):
                system = True
            else:
                sys.exit(1)

    if not system:
        return True, None, False
    elif not is_root:
        # need to re-run with sudo
        return False, user, True
    else:
        msg = f'Please provide the name of your regular (non-root) user'
        user = prompt(msg, default=(get_primary_user() or ''), check_user=True)
        return False, user, False
    return None, None, None


def get_primary_user():
    try:
        return os.environ['SUDO_USER']
    except KeyError:
        pass
    # see if we can find a single user with a home directory
    uids = {x.stat().st_uid for x in Path('/home').iterdir() if x.is_dir()}
    users = []
    for uid in uids:
        try:
            users.append(pwd.getpwuid(uid).pw_name)
        except KeyError:
            # no user for the given uid
            pass
    if len(users) == 1:
        return users[0]
    return None


def confirm(msg, default=None):
    yes = 'y'
    no = 'n'
    if default is not None:
        if default:
            yes = 'Y'
        else:
            no = 'N'
    prompt = (
        f'{msg} [{Fore.LIGHTGREEN}{yes}{Fore.RESET}/{Fore.LIGHTRED}{no}{Fore.RESET}]: '
    )
    while True:
        print(prompt, end='')
        try:
            value = input('').lower().strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        if value in ('y', 'yes'):
            return True
        elif value in ('n', 'no'):
            return False
        elif value == '' and default is not None:
            return default
        else:
            print('invalid input')


def prompt(msg, default=None, check_user=False):
    prompt = msg
    if default is not None:
        prompt = f'{msg} [{Fore.LIGHTWHITE}{default}{Fore.RESET}]'
    while True:
        print(prompt, end=': ')
        try:
            value = input('').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        if value:
            rv = value
        elif default is not None:
            rv = default
        else:
            print('input required')
            continue
        if check_user and rv:
            try:
                user_arg_type(rv)
            except ArgumentTypeError as exc:
                print(exc)
                continue
        break
    return rv


def user_arg_type(value):
    try:
        rec = pwd.getpwnam(value)
    except KeyError:
        raise ArgumentTypeError('invalid user')
    if rec.pw_uid == 0:
        raise ArgumentTypeError('this user is root')
    elif rec.pw_shell == '/bin/false':
        raise ArgumentTypeError('this user has no shell')
    return value


def parse_args():
    parser = ArgumentParser(prog=sys.argv[0])
    parser.add_argument(
        '--user',
        type=user_arg_type,
        help='Primary user of the system (only used for global install)',
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        '--local',
        dest='user_install',
        action='store_true',
        default=None,
        help='Install locally',
    )
    group.add_argument(
        '--global',
        dest='user_install',
        action='store_false',
        default=None,
        help='Install globally',
    )
    args = parser.parse_args()
    if args.user is not None and args.user_install in (None, True):
        print('error: cannot specify --user unless --global is used')
        return None
    if args.user_install is False and os.geteuid() != 0:
        print('error: global install requires root')
        return None
    return args


def main():
    distro = guess_distro()
    if distro not in DISTROS:
        print(
            f'{Fore.LIGHTRED}Unknown distro {Fore.RED}{distro}{Fore.LIGHTRED};'
            f'some automatisms may not work!{Fore.RESET}'
        )

    args = parse_args()
    if args is None:
        return 1

    if args.user_install is None:
        user_install, primary_user, sudo = get_install_mode()
        print()
        if sudo:
            cmd_args = [sys.argv[0], '--global']
            if primary_user:
                cmd_args += ['--user', primary_user]
            cmd = ' '.join(map(shlex.quote, cmd_args))
            print(
                f'Using sudo to become root. If this fails, you can run '
                f'{Fore.LIGHTWHITE}{cmd}{Fore.RESET} as root manually'
            )
            try:
                os.execlp('sudo', 'sudo', '-E', *cmd_args)
            except FileNotFoundError as exc:
                print(f'{Fore.LIGHTRED}sudo failed: {Fore.RED}{exc}{Fore.RESET}')
                return 1
    else:
        user_install, primary_user = args.user_install, args.user

    base_dir = Path(__file__).absolute().parent
    print('running some checks...')
    if not check_root(user_install):
        return 1
    if not check_programs(distro, ['git', 'zsh', 'tmux', 'vim']):
        return 1

    print()
    if user_install:
        target_dir = Path('~/.config/myd0t').expanduser()
    else:
        # make sure files we create/copy are world-readable
        os.umask(0o022)
        target_dir = Path('/opt/myd0t')
    print(f'installing to {Fore.LIGHTWHITE}{target_dir}{Fore.RESET}')
    target_dir.mkdir(parents=True, exist_ok=True)

    # bin is not tied to any specific application (even though it could be),
    # so we just copy the whole thing beforehand
    target_bin_path = target_dir / 'bin'
    if target_bin_path.exists():
        shutil.rmtree(target_bin_path)
    shutil.copytree(base_dir / 'bin', target_bin_path)

    print('installing configs...')
    etc_path = base_dir / 'etc'
    target_etc_path = target_dir / 'etc'
    target_etc_path.mkdir(exist_ok=True)
    install_tmux(
        etc_path / 'tmux', target_etc_path / 'tmux', target_bin_path, user_install
    )
    install_zsh(etc_path / 'zsh', target_etc_path / 'zsh', user_install)
    install_git(
        etc_path / 'git', target_etc_path / 'git', target_bin_path, user_install
    )
    install_editor(etc_path / 'vim', target_etc_path / 'vim', user_install, distro)
    if user_install or primary_user:
        install_dconf(base_dir / 'dconf', user_install, primary_user)
    update_shell(primary_user)
    return 0


if __name__ == '__main__':
    sys.exit(main())
