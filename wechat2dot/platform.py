"""Native OS boundary: private storage, no-follow opens and process locks.

Business logic never emulates POSIX permissions on Windows. Windows creates
owner-only security descriptors and validates ACLs on the opened handle.
"""

import errno
import os
from pathlib import Path
import stat
import sys

import portalocker


def reject_links(path):
    path = Path(os.path.abspath(path))
    # macOS exposes OS-owned aliases /var -> /private/var (and /tmp, /etc).
    # Canonicalize only these exact system aliases, never user symlinks.
    if sys.platform == "darwin" and len(path.parts) > 1:
        alias = path.parts[1]
        if alias in {"var", "tmp", "etc"}:
            system_path = Path("/") / alias
            if (system_path.is_symlink() and system_path.lstat().st_uid == 0
                    and os.readlink(system_path) in {"/private/" + alias, "private/" + alias}):
                path = Path("/private") / path.relative_to("/")
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("symlink_not_allowed")
    return path


def _windows_identity():
    import win32api
    import win32con
    import win32security
    security_handle = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        return win32security.GetTokenInformation(security_handle, win32security.TokenUser)[0]
    finally:
        security_handle.Close()


def _windows_attributes(directory=False):
    import ntsecuritycon
    import pywintypes
    import win32security
    owner = _windows_identity()
    acl = win32security.ACL()
    inheritance = (win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE) if directory else 0
    acl.AddAccessAllowedAceEx(win32security.ACL_REVISION, inheritance, ntsecuritycon.FILE_ALL_ACCESS, owner)
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorOwner(owner, False)
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    descriptor.SetSecurityDescriptorControl(win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED)
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    attributes.bInheritHandle = False
    return attributes


def _windows_check(descriptor):
    import ntsecuritycon
    import win32security
    owner = _windows_identity()
    if descriptor.GetSecurityDescriptorOwner() != owner:
        raise ValueError("unsafe_owner")
    acl = descriptor.GetSecurityDescriptorDacl()
    if acl is None:
        raise ValueError("unsafe_permissions")
    full_access = False
    for index in range(acl.GetAceCount()):
        header, mask, sid = acl.GetAce(index)
        if header[0] != win32security.ACCESS_ALLOWED_ACE_TYPE or sid != owner:
            raise ValueError("unsafe_permissions")
        if not header[1] & win32security.INHERIT_ONLY_ACE:
            full_access |= mask & ntsecuritycon.FILE_ALL_ACCESS == ntsecuritycon.FILE_ALL_ACCESS
    if not full_access:
        raise ValueError("unsafe_permissions")


def check_private_path(path, directory=False):
    path = reject_links(path)
    info = path.lstat()
    if (directory and not stat.S_ISDIR(info.st_mode)) or (not directory and not stat.S_ISREG(info.st_mode)):
        raise ValueError("unsafe_path")
    if not directory and info.st_nlink != 1:
        raise ValueError("unsafe_path")
    if os.name == "nt":
        import win32security
        descriptor = win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
                    win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION)
        _windows_check(descriptor)
    elif info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
        raise ValueError("unsafe_permissions")
    return path


def private_directory(path):
    path = reject_links(path)
    for component in reversed((path, *path.parents)):
        if component.exists():
            if not component.is_dir():
                raise ValueError("directory_required")
            continue
        if os.name == "nt":
            import win32file
            import pywintypes
            try:
                win32file.CreateDirectory(str(component), _windows_attributes(directory=True))
            except pywintypes.error as error:
                if error.winerror != 183:
                    raise OSError(errno.EACCES, "directory_unavailable") from None
        else:
            try:
                component.mkdir(mode=0o700)
            except FileExistsError:
                pass
    return check_private_path(path, directory=True)


def safe_open(path, flags, mode=0o600):
    """Open a regular file without following links on either OS family."""
    path = reject_links(path)
    if os.name != "nt":
        return os.open(path, flags | os.O_NOFOLLOW, mode)
    import msvcrt
    import win32con
    import win32file
    import pywintypes
    access = win32con.GENERIC_READ
    if flags & os.O_RDWR:
        access |= win32con.GENERIC_WRITE
    elif flags & os.O_WRONLY:
        access = win32con.GENERIC_WRITE
    disposition = (win32con.CREATE_NEW if flags & os.O_EXCL else win32con.OPEN_ALWAYS) if flags & os.O_CREAT else win32con.OPEN_EXISTING
    try:
        handle = win32file.CreateFile(str(path), access,
                    win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
                    _windows_attributes(), disposition, win32file.FILE_FLAG_OPEN_REPARSE_POINT, None)
    except pywintypes.error as error:
        code = getattr(error, "winerror", error.args[0] if error.args else None)
        if code in (2, 3):
            raise FileNotFoundError(errno.ENOENT, "file_unavailable") from None
        if code in (80, 183):
            raise FileExistsError(errno.EEXIST, "file_exists") from None
        if code == 5:
            raise PermissionError(errno.EACCES, "access_denied") from None
        raise OSError(errno.EIO, "file_operation_failed") from None
    try:
        info = win32file.GetFileInformationByHandle(handle)
        if info[0] & (win32con.FILE_ATTRIBUTE_DIRECTORY | win32con.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("unsafe_path")
        raw_handle = handle.Detach()
        try:
            return msvcrt.open_osfhandle(raw_handle, flags | os.O_BINARY)
        except BaseException:
            win32file.CloseHandle(raw_handle)
            raise
    finally:
        if handle:
            handle.Close()


def check_private_fd(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("unsafe_path")
    if os.name == "nt":
        import msvcrt
        import win32security
        descriptor = win32security.GetSecurityInfo(msvcrt.get_osfhandle(fd), win32security.SE_FILE_OBJECT,
                    win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION)
        _windows_check(descriptor)
    elif info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("unsafe_permissions")


def lock_exclusive(fd):
    try:
        portalocker.lock(fd, portalocker.LOCK_EX | portalocker.LOCK_NB)
    except portalocker.exceptions.LockException:
        raise BlockingIOError(errno.EWOULDBLOCK, "already_running") from None


def sync_directory(path):
    # Windows file data is fsynced before atomic replace; CRT has no directory
    # descriptor fsync. SQLite's own native WAL durability remains enabled.
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
