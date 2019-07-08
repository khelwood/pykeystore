#!/usr/bin/env python3

"""
Interactive script for reviewing Java key stores.

Example 1 (fetching a keystore):
  $ ./pykeystore.py
  no keystore > fetch /path/to/store.jks
  cp /path/to/store.jks _store.jks
  _store.jks > ls
  Enter keystore password:
  Your keystore contains 1 entry
  ...

Example 2 (opening an existing local keystore)
  $ ./pykeystore.py mykeystore.jks
  mykeystore.jks > ls
  ...

Example 3 (creating a new keystore)
  $ ./pykeystore.py newkeystore.jks
  newkeystore.jks > add bananas banana.pem
  ...
  Certificate was added to keystore
  newkeystore.jks > quit

Commands include:
  adding, removing and renaming aliases (certificates) in the keystore
  listing and exporting certificates
  downloading a certificate from a named site
  help - list commands
"""

import sys
import os
import subprocess
import argparse
import readline
import shutil
import traceback
import shlex
from getpass import getpass
from collections import OrderedDict

STORETYPE = 'jks'

def execute(args, **kwargs):
    c = subprocess.run(args, **kwargs)
    if c.returncode:
        print(in_red("Command finished with return code %s"%c.returncode))
        return False
    return True

def arg_item(item):
    if ' ' in item or '"' in item or "'" in item:
        return repr(item)
    return item

def echo_command(cmd):
    if not isinstance(cmd, str):
        cmd = ' '.join(map(arg_item, cmd))
    print(in_green(cmd))

def pwinput():
    try:
        return getpass('Enter keystore password: ')
    except KeyboardInterrupt:
        print("^C")
        return None
    except EOFError:
        print("^D")
        return None

class PasswordHolder:
    def __init__(self, password=None):
        self.password = password
    @property
    def args(self):
        if not self.password:
            self.password = pwinput()
            if self.password is None:
                return None
        return ['-storepass', self.password]

class KeyTool:
    def __init__(self, keystore, password_holder=None, alias=None):
        self.keystore = keystore
        if isinstance(password_holder, str):
            self.password_holder = PasswordHolder(password_holder)
        else:
            self.password_holder = password_holder or PasswordHolder()
        self.alias = alias
    @property
    def password(self):
        return self.password_holder.password
    @password.setter
    def password(self, value):
        self.password_holder.password = value
    def alias(self, alias):
        return KeyTool(self.keystore, self.password_holder, alias)
    __getitem__ = alias
    def execute(self, *args):
        if not self.keystore:
            print("No keystore is selected.")
            return False
        command = ['keytool'] + list(args) + ['-keystore', self.keystore]
        if self.alias:
            command += ['-alias', self.alias]
        echo_command(command)
        pwargs = self.password_holder.args
        if pwargs is None:
            print("Cancelled.")
            return None
        return execute(command + pwargs)
    def list(self, verbose=False):
        if verbose:
            self.execute('-list', '-v')
        else:
            self.execute('-list')
    def require_alias(self):
        if not self.alias:
            raise ValueError("Need an alias for this command.")
    def rename(self, new_alias):
        self.require_alias()
        self.execute('-changealias', '-destalias', new_alias)
    def __delitem__(self, alias):
        self[alias].execute('-delete')
    def __setitem__(self, alias, filename):
        self[alias].execute('-import', '-file', filename, '-storetype', STORETYPE)
    def export(self, filename=None):
        self.require_alias()
        if filename:
            self.execute('-exportcert', '-rfc', '-file', filename)
        else:
            self.execute('-exportcert', '-rfc')

def in_green(text):
    return "\033[92m%s\033[0m"%text

def in_red(text):
    return "\033[91m%s\033[0m"%text

def suggest_help():
    print("Type \"help\" for instructions.")

COMMANDS = OrderedDict()

BadUsage = object()

def booladd(items, x):
    n = len(items)
    items.add(x)
    return len(items) > n

def uniq(items):
    s = set()
    for x in items:
        if booladd(s, x):
            yield x

def command(*args, usage):
    def command(f):
        for arg in args:
            COMMANDS[arg] = f
        f.usage = usage
        return f
    return command

@command('fetch', usage='fetch path/to/store.jks : fetch a key store')
def fetch_cmd(tool, args):
    if len(args)!=1:
        return BadUsage
    try:
        path = os.path.expanduser(args[0])
        workingfile = '_store.jks'
        print(in_green('copyfile(%r, %r)'%(path, workingfile)))
        shutil.copyfile(path, workingfile)
        tool.keystore = workingfile
        return True
    except Exception:
        print(traceback.format_exc())
    print(in_red("Command failed"))
    return False

@command('put', usage='put path/to/store.jks : copy out a key store')
def put_cmd(tool, args):
    if len(args)!=1:
        return BadUsage
    if not tool.keystore:
        print("No keystore is selected.")
        return
    path = os.path.expanduser(args[0])
    print(in_green('copyfile(%r, %r)'%(tool.keystore, path)))
    try:
        shutil.copyfile(tool.keystore, path)
        return True
    except Exception:
        print(traceback.format_exc())
    print(in_red("Command failed"))
    return False

@command('ls', usage='ls [-l] : list contents [verbosely]')
def list_cmd(tool, args):
    if not args:
        return tool.list(False)
    if len(args)==1 and args[0]=='-l':
        return tool.list(True)
    return BadUsage

@command('ll', usage='ll : alias for ls -l')
def ll_cmd(tool, args):
    if args:
        return BadUsage
    tool.list(True)

@command('x', 'export', usage='export / x ALIAS [FILE] : export alias [to file]')
def export_cmd(tool, args):
    if len(args)==1:
        alias = args[0]
        fn = None
    elif len(args)==2:
        alias,fn = args
    else:
        return BadUsage
    if fn and not confirm_overwrite(fn):
        return
    return tool[alias].export(fn)

@command('rm', 'delete', usage='delete / rm ALIAS : delete an alias')
def delete_cmd(tool, args):
    if len(args)!=1:
        return BadUsage
    del tool[args[0]]

@command('add', usage='add ALIAS FILE : import an alias from a file')
def add_cmd(tool, args):
    if len(args)!=2:
        return BadUsage
    alias, fn = args
    tool[alias] = fn

@command('rename', usage='rename OLD_ALIAS NEW_ALIAS : rename an existing alias')
def rename_cmd(tool, args):
    if len(args)!=2:
        return BadUsage
    old_alias, new_alias = args
    tool[old_alias].rename(new_alias)

@command('password', usage='password : prompt for a new keytool password')
def password_cmd(tool, args):
    if args:
        return BadUsage
    tool.password = pwinput()

@command('download', 'dl', usage='download / dl HOST PORT [FILE]: download certificate [to file]')
def download_cmd(tool, args):
    if len(args)==2:
        host, port = args
        pemfile = None
    elif len(args)==3:
        host, port, pemfile = args
    else:
        return BadUsage
    try:
        port = int(port)
    except ValueError:
        return BadUsage
    servername = '%s:%s'%(host, port)
    cmd = ('openssl', 's_client', '-servername', host, '-connect', servername)
    echo_command(cmd)
    if pemfile and not confirm_overwrite(pemfile):
        return
    tempfile = '_temp_file'
    verb = "Overwriting" if os.path.exists(tempfile) else "Writing"
    print("%s intermediate file %r"%(verb, tempfile))
    with open(tempfile, 'w') as fout:
        if not execute(cmd, input='q', encoding='ascii', stdout=fout):
            return
    cmd = ['openssl', 'x509', '-outform', 'pem']
    if pemfile:
        cmd += ['-out', pemfile]
    echo_command(cmd)
    with open(tempfile, 'r') as fin:
        if not execute(cmd, stdin=fin):
            return
    if pemfile:
        print("Output to %s"%pemfile)
    

@command('h', 'help', usage='help / h : show help')
def help_cmd(tool, args):
    if args:
        return BadUsage
    print("Commands:")
    print('  '+'\n  '.join([f.usage for f in uniq(COMMANDS.values())]))
    print()
    
@command('q','quit','^d', usage='quit / q : exit program')
def quit_cmd(tool, args):
    if args:
        return BadUsage
    return 'exit'

def run_command(tool, cmd, args):
    f = COMMANDS.get(cmd)
    if not f:
        print("Command not recognised.")
        return suggest_help()
    r = f(tool, args)
    if r is BadUsage:
        print("Usage:", f.usage)
    return r

def menu(tool):
    prompt = '%s > '%(tool.keystore or 'no keystore')
    try:
        choice = input(prompt).strip()
    except EOFError:
        print()
        return 'exit'
    except KeyboardInterrupt:
        print(' ^C\n(Use "quit" or ^D to quit.)')
        return None
    print()
    if not choice:
        return suggest_help()
    cs = shlex.split(choice)
    cmd = cs[0].lower()
    args = cs[1:]
    return run_command(tool, cmd, args)

def confirm_overwrite(filename):
    if not os.path.exists(filename):
        return True
    print("File exists: %r"%filename)
    return confirm("Overwrite?")

def confirm(message=None):
    if message is not None:
        print(message)
    while True:
        line = input('>> ').strip().lower()
        if line in ('y', 'yes'):
            return True
        if line in ('n', 'no'):
            return False
        print("Please enter y or n.")
        

def main():
    intro, _, outro = __doc__.partition('\n\n')
    parser = argparse.ArgumentParser(description=intro, epilog=outro,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('store', nargs='?', help='keystore file')
    parser.add_argument('--password', '-p', help='password', default=None)
    args = parser.parse_args()
    k = KeyTool(args.store, args.password)
    print()
    suggest_help()
    while menu(k)!='exit':
        pass
    
if __name__ == '__main__':
    main()
