"""Owner-only permissions for Workbench-managed credential storage."""

import os


def protect(path):
    if path.is_symlink() or path.is_junction():
        raise ValueError(
            f"Refusing to change permissions on a linked path: {path.name}"
        )
    directory = path.is_dir()
    if os.name != "nt":
        path.chmod(0o700 if directory else 0o600)
        return

    import ntsecuritycon
    import win32api
    import win32con
    import win32security as sec

    token = sec.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        user = sec.GetTokenInformation(token, sec.TokenUser)[0]
    finally:
        token.Close()
    identities = [
        user,
        sec.CreateWellKnownSid(sec.WinLocalSystemSid),
        sec.CreateWellKnownSid(sec.WinBuiltinAdministratorsSid),
    ]
    flags = (
        (win32con.CONTAINER_INHERIT_ACE | win32con.OBJECT_INHERIT_ACE)
        if directory
        else 0
    )
    desired = sec.ACL()
    for identity in identities:
        desired.AddAccessAllowedAceEx(
            sec.ACL_REVISION, flags, ntsecuritycon.FILE_ALL_ACCESS, identity
        )
    current = sec.GetNamedSecurityInfo(
        str(path), sec.SE_FILE_OBJECT, sec.DACL_SECURITY_INFORMATION
    )
    old = current.GetSecurityDescriptorDacl()
    control, _ = current.GetSecurityDescriptorControl()
    if (
        old
        and control & sec.SE_DACL_PROTECTED
        and old.GetAceCount() == desired.GetAceCount()
    ):
        if all(old.GetAce(i) == desired.GetAce(i) for i in range(old.GetAceCount())):
            return
    sec.SetNamedSecurityInfo(
        str(path),
        sec.SE_FILE_OBJECT,
        sec.DACL_SECURITY_INFORMATION | sec.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        desired,
        None,
    )
