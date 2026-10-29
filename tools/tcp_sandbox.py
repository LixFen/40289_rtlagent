"""Trusted Linux acceptance tool: kernel TCP port allowlist, not a network namespace.

Landlock ABI 4 covers TCP connect ports, not destination IPs or UDP. Never claim
this is complete network isolation. A failure to enforce the policy is fatal.
Reference: https://www.kernel.org/doc/html/v6.8/userspace-api/landlock.html
"""
import argparse
import ctypes
import json
import os
import platform
from pathlib import Path


class Ruleset(ctypes.Structure):
    _fields_ = [('handled_access_fs', ctypes.c_uint64), ('handled_access_net', ctypes.c_uint64)]


class PortRule(ctypes.Structure):
    _fields_ = [('allowed_access', ctypes.c_uint64), ('port', ctypes.c_uint64)]


def landlock_abi():
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise RuntimeError('TCP sandbox requires Linux x86_64 with Landlock ABI >= 4')
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(444, 0, 0, 1)
    if abi < 4:
        raise RuntimeError('Landlock TCP policy unavailable; ABI='+str(abi))
    return abi


def restrict_tcp(ports, ephemeral_range=None):
    ports = sorted(set(ports))
    if any(not 1 <= port <= 65535 for port in ports):
        raise ValueError('Allowed TCP ports must be between 1 and 65535')
    if ephemeral_range is not None:
        low, high = ephemeral_range
        if not 1 <= low <= high <= 65535:
            raise ValueError('Invalid ephemeral TCP port range')
    abi = landlock_abi()
    libc = ctypes.CDLL(None, use_errno=True)
    def checked(result):
        if result < 0:
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number))
        return result
    connect_tcp = 2
    rules = Ruleset(0, connect_tcp)
    fd = checked(libc.syscall(444, ctypes.byref(rules), ctypes.sizeof(rules), 0))
    try:
        all_ports = set(ports)
        if ephemeral_range is not None:
            all_ports.update(range(low, high+1))
        for port in sorted(all_ports):
            rule = PortRule(connect_tcp, port)
            checked(libc.syscall(445, fd, 2, ctypes.byref(rule), 0))
        checked(libc.prctl(38, 1, 0, 0, 0))  # PR_SET_NO_NEW_PRIVS
        checked(libc.syscall(446, fd, 0))
    finally:
        os.close(fd)
    return dict(kind='Landlock TCP connect-port allowlist', abi=abi, allowed_tcp_ports=ports,
                allowed_ephemeral_range=ephemeral_range, udp_restricted=False, ip_address_restricted=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-tcp', type=int, action='append', default=[])
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--allow-ephemeral', action='store_true',
                        help='allow the system ephemeral range for XSim private TCP channels; applies to all IPs')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.probe:
        print(json.dumps({'landlock_abi': landlock_abi()}))
        return
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('a command is required after --')
    ephemeral = None
    if args.allow_ephemeral:
        ephemeral = tuple(map(int, Path('/proc/sys/net/ipv4/ip_local_port_range').read_text().split()))
    policy = restrict_tcp(args.allow_tcp, ephemeral)
    for key in list(os.environ):
        if key.lower() in ('http_proxy', 'https_proxy', 'all_proxy'):
            del os.environ[key]
    print(json.dumps(policy), flush=True)
    os.execvp(command[0], command)


if __name__ == '__main__':
    main()
