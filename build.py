#!/usr/bin/env python

"""
    The MIT License (MIT)

    Copyright (c) 2017 Joel Hoener <athre0z@zyantific.com>
    
    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:
    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.
    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import sys
import os
import errno
import argparse
import glob
import shutil
import re

from subprocess import Popen, PIPE
from distutils.spawn import find_executable


def get_cmake_gen(target_version, custom_gen):
    if custom_gen:
        return ['-G', custom_gen]
    if os.name == 'posix':
        return ['-G', 'Unix Makefiles']
    elif os.name == 'nt':
        #gen = 'Visual Studio ' + (
        #    '10' if target_version[0] <= 6 and target_version[1] <= 8 else '14'
        #)
        #return (gen + ' Win64') if target_version >= (7, 0) else gen
        return ['-A', 'x64'] if target_version >= (7, 0) else ['-A', 'Win32']
    else:
        assert False

if __name__ == '__main__':
    #
    # Parse arguments
    #
    parser = argparse.ArgumentParser(
        description='Build script compiling and installing the plugin.'
    )
    
    target_args = parser.add_argument_group('target configuration')
    target_args.add_argument(
        '--ida-sdk', '-i', type=str, required=True,
        help='Path to the IDA SDK'
    )
    target_args.add_argument(
        '--target-version', '-t', required=False,
        help='IDA versions to build for (e.g. 6.9). If not supplied, we auto detects it.'
    )
    target_args.add_argument(
        '--ida-path', type=str, required=False,
        help='Path of IDA installation, used for installing the plugin. '
             'On unix-like platforms, also required for linkage.'

    )
    target_args.add_argument(
        '--ea', required=False, choices=[32, 64], type=int,
        help='The IDA variant (ida/ida64, sizeof(ea_t) == 4/8) to build for. '
             'If omitted, build both.'
    )

    parser.add_argument(
        '--release', action='store_true', default=False,
        help='Do release build'
    )

    parser.add_argument(
        '--arch', type=str, required=False, choices=["x86_64", "arm64"],
        help='Specify architecture to build on macOS, defaults to build both'
    )
    
    parser.add_argument(
        '--install', action='store_true', default=False,
        help='Do not execute install target'
    )
    parser.add_argument(
        '--gen', default='', type=str,
        help='Custom generator for CMake (e.g. Ninja)'
    )
    args, cmake_args = parser.parse_known_args()

    def print_usage(error=None):
        parser.print_usage()
        if error:
            print(error)
        exit()

    # Parse target version
    if args.target_version is None:
        with open(args.ida_sdk + '/allmake.mak', 'r') as f:
            allmake = f.read()
        try:
            verMajor = re.findall(r'\nIDAVER_MAJOR:=(\d+)\n', allmake)[0]
            verMinor = re.findall(r'\nIDAVER_MINOR:=(\d+)\n', allmake)[0]
            target_version = (int(verMajor), int(verMinor))
        except (ValueError, IndexError):
            print_usage('[-] Failed to parse major version in allmake.mak, please manually specify --target-version')
    else:
        target_version = args.target_version.strip().split('.')
        try:
            target_version = int(target_version[0]), int(target_version[1])
        except (ValueError, IndexError):
            print_usage('[-] Invalid version format, expected something like "6.5"')

    # Supported platform?
    if os.name not in ('nt', 'posix'):
        print('[-] Unsupported platform')

    #
    # Find tools
    #
    cmake_bin = find_executable('cmake')
    if not cmake_bin:
        print_usage('[-] Unable to find CMake binary')
    

    build_type = 'Release' if args.release else 'Debug'

    platform = {
        "win32": "win",
        "darwin": "macos",
        "linux": "linux",
    }[sys.platform]
    
    #
    # Build targets
    #
    for arch in (args.arch, ) if platform != 'macos' or args.arch else ('x86_64', 'arm64'):
        envtype = platform if platform != 'macos' else platform + "-" + arch
        triple = '{}.{}-{}-{}'.format(
            *target_version, 
            envtype,
            build_type,
            )
        output_dir = 'output-' + triple
        try:
            os.mkdir(output_dir)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise

        ALL_EAS = (32, 64) if target_version[0] < 9 else (64, )
        for ea in (args.ea,) if args.ea else ALL_EAS:
            build_dir = 'build-{}-{}'.format(triple, ea)
            try:
                os.mkdir(build_dir)
            except OSError as e:
                if e.errno != errno.EEXIST:
                    raise

            # Run cmake
            cmake_cmd = [
                cmake_bin,
                '-DIDA_SDK=' + args.ida_sdk,
                *get_cmake_gen(target_version, args.gen.strip()),
                '-DIDA_BINARY_64=' + ('ON' if target_version >= (7, 0) else 'OFF'),
                '-DCMAKE_INSTALL_PREFIX=' + os.path.abspath(output_dir),
                '-DCMAKE_BUILD_TYPE=' + ("RelWithDebInfo" if args.release else "Debug"),
            ]

            if args.ida_path:
                cmake_cmd.append('-DIDA_INSTALL_DIR=' + args.ida_path)

            if ea == 64:
                cmake_cmd.append('-DIDA_EA_64=TRUE')
            
            if platform == "macos":
                cmake_cmd.append('-DIDA_CURRENT_PROCESSOR=' + arch)

            cmake_cmd += cmake_args
            cmake_cmd.append('..')

            print('CMake command:')
            print(' '.join("'%s'" % x if ' ' in x else x for x in cmake_cmd))

            proc = Popen(cmake_cmd, cwd=build_dir)
            if proc.wait() != 0:
                print('[-] CMake failed, giving up.')
                exit()

            # Build plugin
            cmake_cmd = [
                cmake_bin,
                '--build', '.', '--target', 'install',
            ]
            if args.release:
                cmake_cmd += ["--config", "RelWithDebInfo"]
            proc = Popen(cmake_cmd, cwd=build_dir)
            if proc.wait() != 0:
                print('[-] Build failed, giving up.')
                exit()
        
    if args.install and args.ida_path:
        if not args.arch and platform == 'macos':
            print("[-] You should specify arch when installing on macOS")
            exit()
        print('[+] Installing...')
        shutil.copytree(output_dir, args.ida_path + '/plugins', dirs_exist_ok=True)

    print('[+] Done!')
