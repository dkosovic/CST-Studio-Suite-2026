import datetime
import pathlib
import fabric
import invoke
import os
import paramiko
import socket
from . import hpc_json

class Connection():
    def __init__(self):
        self.user = ""
        self.pkey = ""
        self.host = ""
        self.con = None

    def open(self, user, host, pkey):
        self.user = user
        self.pkey = pkey
        self.host = host
        if self.con:
            self.con.close()
        self.con = fabric.Connection(user = self.user, host = self.host, connect_kwargs = {"key_filename" : self.pkey})
        ret = self.con.open()
        return ret

    def close(self):
        self.con.close()

    def run(self, cmd):
        return self.con.run(cmd)

    def put(self, src, dst):
        return self.con.put(src, dst)


class Settings():
    def __init__(self, settings_json):
        self.settings = settings_json

    def username(self):
        return self.settings['cluster_settings']['username']

    def hostname(self):
        return self.settings['cluster_settings']['hostname']

    def private_key_file(self):
        return self.settings['cluster_settings']['private_key_file']

    def utilities_dir(self):
        return self.settings['cluster_settings']['utilities_dir']

    def working_dir(self):
        return self.settings['cluster_settings']['working_dir']

    def filename(self):
        return self.settings['filename']

    def run_option(self):
        if self.settings['solver'] == 'Single Solver':
            return 'Active_solver'
        if self.settings['solver'] == 'Parameter Sweep':
            return 'Parameter_sweep'
        if self.settings['solver'] == 'Optimizer':
            return 'Optimizer'
        if self.settings['solver'] == 'Schematic Tasks':
            return 'DS_run'
        return 'Active_solver'

    def type(self):
        if self.settings['type'] == 'Single node':
            return 'Single node'
        if self.settings['type'] == 'MPI':
            return 'MPI_computing'
        if self.settings['type'] == 'DC':
            return 'Distributed_computing'
        return 'Single node'

    def gpus(self):
        return self.settings['gpu']

    def cores(self):
        return self.settings['core']

    def nodes(self):
        return self.settings['node']

    def queue(self):
        return self.settings['queue']

def open_connection(con, settings, mbox):
    user = settings.username()
    host = settings.hostname()
    pkey = settings.private_key_file()
    wdir = settings.working_dir()
    udir = settings.utilities_dir()

    if (not user) or (not host) or (not pkey) or (not wdir) or (not udir):
        msg = "SSH setup has not been finished"
        mbox.appendPlainText(msg)
        return -1

    # ssh: 1. open connection
    try:
        con.open(user, host, pkey)
    except paramiko.ssh_exception.AuthenticationException as err:
        msg  = '<span style="color:darkred">'
        msg += 'AuthenticationException'
        msg += '</span><br>'
        msg += 'Check user name, authorized_keys, etc.'
        mbox.appendHtml(msg)
        return -1
    except paramiko.ssh_exception.SSHException as err:
        msg  = '<span style="color:darkred">'
        msg += 'SSHException'
        msg += '</span><br>'
        msg += 'Check SSH setup, private and public keys, etc.'
        mbox.appendHtml(msg)
        return -1
    except FileNotFoundError as err:
        msg  = '<span style="color:darkred">'
        msg += 'FileNotFoundError'
        msg += '</span><br>'
        msg += 'No such file or directory: ' + '\'' + err.filename + '\''
        mbox.appendHtml(msg)
        return -1
    except socket.gaierror as err:
        msg  = '<span style="color:darkred">'
        msg += 'GetAddressInfoException'
        msg += '</span><br>'
        msg += 'Check the hostname, network connection, etc.'
        mbox.appendHtml(msg)
        return -1
    except Exception as err:
        mbox.appendPlainText(str(err))
        return -1

    # ssh: 2. run hostname connection test
    try:
        ret = con.run('hostname')
        if ret.stderr:
            mbox.appendPlainText(ret.stderr)
            if not ret.return_code == 0:
                return ret.return_code
    except Exception as err:
        mbox.appendPlainText(str(err))
        return -1

    # ssh: 3. run cluster utilities directory check
    try:
        ret = con.run('ls -d ' + '"' + udir + '"')
        if ret.stderr:
            mbox.appendPlainText(ret.stderr)
            return ret.return_code
    except invoke.exceptions.UnexpectedExit as err:
        msg  = '<span style="color:darkred">'
        msg += 'UnexpectedExit'
        msg += '</span><br>'
        msg += err.result.stderr
        mbox.appendHtml(msg)
        return -1
    except Exception as err:
        mbox.appendPlainText(str(err))
        return -1

    # ssh: 4. run working directory check
    try:
        ret = con.run('ls -d ' + '"' + wdir + '"')
        if ret.stderr:
            mbox.appendPlainText(ret.stderr)
            return ret.return_code
    except invoke.exceptions.UnexpectedExit as err:
        msg  = '<span style="color:darkred">'
        msg += 'UnexpectedExit'
        msg += '</span><br>'
        msg += err.result.stderr
        mbox.appendHtml(msg)
        return -1
    except Exception as err:
        mbox.appendPlainText(str(err))
        return -1

    return 0

def submit(con, settings, mbox):
    wdir = settings.working_dir()
    udir = settings.utilities_dir()
    proj = settings.filename()

    # create calculation directory
    cst_project_name = pathlib.Path(proj).stem
    uid = datetime.datetime.now().strftime('%Y%m%d%H%M%S')

    dst_file_path = pathlib.PurePosixPath(wdir).joinpath(cst_project_name + '_' + uid, cst_project_name + ".cst")
    dst_file = str(dst_file_path)

    dst_dir_path = dst_file_path.parent
    dst_dir = str(dst_dir_path)

    # ssh: run make destination directory
    try:
        ret = con.run('mkdir ' + '"' + dst_dir + '"')
        if ret.stderr:
            mbox.appendPlainText(ret.stderr)
            return ret.return_code
    except Exception as err:
        msg  = '\n'
        msg += 'Directory \"' + dst_dir + '\" already exists.\n'
        msg += 'Rename or remove the already existing target directory on the cluster.'
        mbox.appendPlainText(msg)
        return -1

    # ssh: put CST project file (and more) to remote compute node
    try:
        root = os.path.splitext(proj)[0]
        extensions = ['.cst', '.inp', '_tosca.par', '_topology_init.onf']
        for ext in extensions:
            src_file = root + ext
            if os.path.isfile(src_file):
                con.put(src_file, dst_dir)
    except Exception as err:
        mbox.appendPlainText(str(err))
        return -1

    # run CST project
    cst_job_submit = pathlib.PurePosixPath(udir).joinpath('cst_job_submit')
    cst_job_submit = '"' + str(cst_job_submit) + '"'
    cst_job_submit += ' -b'
    cst_job_submit += ' -s ' + settings.run_option()
    if settings.gpus() > 0:
        cst_job_submit += ' -g ' + str(settings.gpus())
    if settings.cores() > 0:
        cst_job_submit += ' --num-cores ' + str(settings.cores())
    if (settings.type() != 'Single node'):
        cst_job_submit += ' -a ' + settings.type()
        cst_job_submit += ' -n ' + str(settings.nodes())
    cst_job_submit += ' -q ' + settings.queue()
    cst_job_submit += ' -m ' + '"' + dst_file + '"'

    # ssh: run cst_job_submit
    try:
        ret = con.run(cst_job_submit)
        if ret.stdout:
            mbox.appendPlainText('\n' + ret.stdout)
        if ret.stderr:
            mbox.appendPlainText(ret.stderr)
            return ret.return_code
    except Exception as err:
        mbox.appendPlainText(str(err))
        return -1

    return 0

def get_queues(con, settings, mbox):
    udir = settings.utilities_dir()

    cst_job_submit = pathlib.PurePosixPath(udir).joinpath('cst_job_submit')
    cst_job_submit = '"' + str(cst_job_submit) + '"'
    cst_job_submit += ' --get-available-queues'

    try:
        ret = con.run(cst_job_submit)
        if ret.stderr:
            mbox.appendPlainText(ret.stderr)
            return ret.return_code
    except Exception as err:
        mbox.appendPlainText(str(err))
        return ""

    return ret.stdout

def get_scheduler(con, settings, mbox):
    udir = settings.utilities_dir()

    cst_job_submit = pathlib.PurePosixPath(udir).joinpath('cst_job_submit')
    cst_job_submit = '"' + str(cst_job_submit) + '"'
    cst_job_submit += ' --get-queuesys-name'

    try:
        ret = con.run(cst_job_submit)
        if ret.stderr:
            mbox.appendPlainText(ret.stderr)
            return ret.return_code
    except Exception as err:
        mbox.appendPlainText(str(err))
        return ""

    return ret.stdout

def main():
    submit()

if __name__ == "__main__":
    main()
