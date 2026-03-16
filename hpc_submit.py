import datetime
import pathlib
import fabric
import invoke
import os
import paramiko
import socket
import getpass
from . import hpc_json

try:
    from qtpy.QtWidgets import QApplication
    HAS_QT = True
except ImportError:
    HAS_QT = False

class Connection():
    def __init__(self):
        self.user = ""
        self.pkey = ""
        self.host = ""
        self.con = None
        self.totp_handler = None
        self._interactive_prompt_seen = False
        self._interactive_cancelled = False

    def _keyboard_interactive_handler(self, title, instructions, prompt_list):
        """Handle keyboard-interactive authentication for 2FA (DUO/TOTP)"""
        if self.totp_handler is None:
            return []

        context_lines = []
        if title:
            context_lines.append(str(title).strip())
        if instructions:
            context_lines.append(str(instructions).strip())

        responses = []
        for prompt_tuple in prompt_list:
            self._interactive_prompt_seen = True
            # prompt_tuple is (prompt_text, show_input)
            prompt_text = prompt_tuple[0] if isinstance(prompt_tuple, tuple) else str(prompt_tuple)

            # Always ask the user for keyboard-interactive responses instead of auto-skipping prompts.
            challenge = str(prompt_text).strip() or "Authentication response:"
            full_prompt = "\n\n".join([line for line in context_lines if line])
            if full_prompt:
                full_prompt += "\n\n"
            full_prompt += challenge

            response = self.totp_handler(full_prompt)
            if response is None:
                self._interactive_cancelled = True
                return []
            responses.append(response)

        return responses

    def open(self, user, host, pkey, totp_handler=None):
        self.user = user
        self.pkey = pkey
        self.host = host
        self.totp_handler = totp_handler

        if self.con:
            self.con.close()

        # Check if file exists
        if not os.path.exists(self.pkey):
            raise FileNotFoundError(f"Private key file not found: {self.pkey}")

        # Read first line to detect key format
        with open(self.pkey, 'r', encoding='utf-8', errors='ignore') as f:
            first_line = f.readline().strip()

        # Check for public key (common mistake!)
        if first_line.startswith(("ssh-rsa", "ssh-ed25519", "ecdsa-", "ssh-dss")):
            raise paramiko.ssh_exception.SSHException(
                "ERROR: This is a PUBLIC key file, not a PRIVATE key!\n\n"
                "You need to select the PRIVATE key file (without .pub extension).\n\n"
                "Public keys look like:\n"
                "  ssh-rsa AAAAB3NzaC1yc2EAAA...\n\n"
                "Private keys look like:\n"
                "  -----BEGIN OPENSSH PRIVATE KEY-----\n"
                "  or\n"
                "  -----BEGIN RSA PRIVATE KEY-----\n\n"
                "The public key goes on the SERVER in ~/.ssh/authorized_keys\n"
                "The private key stays on YOUR COMPUTER and is used to connect.\n\n"
                "Look for a file with the SAME name but WITHOUT the .pub extension."
            )

        # Check for PuTTY format
        if first_line.startswith("PuTTY-User-Key-File"):
            raise paramiko.ssh_exception.SSHException(
                "PuTTY .ppk key format detected. Please convert to OpenSSH format using:\n\n"
                "Option 1 - PuTTYgen (GUI):\n"
                "1. Open PuTTYgen\n"
                "2. Load your .ppk file\n"
                "3. Go to Conversions → Export OpenSSH key\n"
                "4. Save as 'id_rsa' (or another name)\n"
                "5. Use that file in the settings\n\n"
                "Option 2 - Command line:\n"
                "puttygen yourkey.ppk -O private-openssh -o id_rsa"
            )

        # Prepare connection kwargs
        connect_kwargs = {
            "key_filename": self.pkey,
            "look_for_keys": False,  # Don't search default SSH locations
            "allow_agent": False,     # Don't use SSH agent
        }

        # Try to pre-load the key to check if it needs a passphrase
        key_obj = None
        passphrase = None
        last_error = None

        # Detect key type and check for passphrase requirement
        key_types = [
            (paramiko.RSAKey, "RSA"),
            (paramiko.Ed25519Key, "Ed25519"),
            (paramiko.ECDSAKey, "ECDSA"),
            (paramiko.DSSKey, "DSS")
        ]

        for key_class, key_name in key_types:
            try:
                key_obj = key_class.from_private_key_file(self.pkey)
                # Successfully loaded without passphrase
                connect_kwargs["pkey"] = key_obj
                del connect_kwargs["key_filename"]
                break
            except paramiko.ssh_exception.PasswordRequiredException:
                # Key needs passphrase - prompt user
                if totp_handler:
                    passphrase = totp_handler(f"Enter passphrase for {key_name} private key:")
                    if passphrase:
                        try:
                            key_obj = key_class.from_private_key_file(self.pkey, password=passphrase)
                            connect_kwargs["pkey"] = key_obj
                            del connect_kwargs["key_filename"]
                            break
                        except Exception as e:
                            last_error = e
                            # Wrong passphrase or other error, try next key type
                            continue
                    else:
                        raise paramiko.ssh_exception.AuthenticationException("Passphrase required but not provided")
                else:
                    raise paramiko.ssh_exception.SSHException(f"{key_name} key requires a passphrase but no handler provided")
            except Exception as e:
                last_error = e
                # Not this key type, try next
                continue

        # If no key could be loaded, raise the last error with helpful context
        if key_obj is None and "key_filename" not in connect_kwargs:
            error_msg = f"Could not load private key from {self.pkey}.\n"
            error_msg += f"First line of file: {first_line[:50]}...\n\n"
            error_msg += "Supported formats:\n"
            error_msg += "• OpenSSH format (starts with '-----BEGIN OPENSSH PRIVATE KEY-----')\n"
            error_msg += "• Traditional PEM format (starts with '-----BEGIN RSA PRIVATE KEY-----')\n\n"
            if last_error:
                error_msg += f"Last error: {str(last_error)}"
            raise paramiko.ssh_exception.SSHException(error_msg)

        # Create Fabric connection
        def build_connection(connection_kwargs):
            self.con = fabric.Connection(
                user=self.user,
                host=self.host,
                connect_kwargs=connection_kwargs
            )
            # Set host key policy to auto-accept (prevents unknown host errors)
            self.con.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        def open_with_interactive_patch():
            self._interactive_prompt_seen = False
            self._interactive_cancelled = False

            if totp_handler is None:
                return self.con.open()

            # Store original auth_interactive_dumb
            original_auth_interactive_dumb = paramiko.Transport.auth_interactive_dumb

            def patched_auth_interactive_dumb(transport_self, username, handler=None, submethods=''):
                # Call our handler instead of the default stdin-based one
                return transport_self.auth_interactive(username, self._keyboard_interactive_handler, submethods)

            try:
                # Patch Paramiko's default keyboard-interactive handler
                paramiko.Transport.auth_interactive_dumb = patched_auth_interactive_dumb
                return self.con.open()
            finally:
                # Restore original
                paramiko.Transport.auth_interactive_dumb = original_auth_interactive_dumb

        build_connection(connect_kwargs)

        try:
            ret = open_with_interactive_patch()
        except paramiko.ssh_exception.AuthenticationException as first_auth_error:
            # RHEL9 deployments may require account password first, then Duo passcode.
            # If no keyboard-interactive prompts appeared yet, retry once with explicit password.
            if (totp_handler is None) or self._interactive_cancelled or self._interactive_prompt_seen:
                raise

            password_prompt = f"SSH password for {self.user}@{self.host}:"
            ssh_password = totp_handler(password_prompt)
            if not ssh_password:
                raise first_auth_error

            retry_connect_kwargs = dict(connect_kwargs)
            retry_connect_kwargs["password"] = ssh_password
            build_connection(retry_connect_kwargs)
            ret = open_with_interactive_patch()

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

def open_connection(con, settings, mbox, totp_callback=None):
    user = settings.username()
    host = settings.hostname()
    pkey = settings.private_key_file()
    wdir = settings.working_dir()
    udir = settings.utilities_dir()

    if (not user) or (not host) or (not pkey) or (not wdir) or (not udir):
        msg = "SSH setup has not been finished"
        mbox.appendPlainText(msg)
        return -1

    # Define 2FA handler that prompts through GUI
    def totp_handler(prompt_text):
        if totp_callback:
            # Process events to keep GUI responsive
            if HAS_QT:
                QApplication.processEvents()
            return totp_callback(prompt_text)
        return None

    # Keep GUI responsive
    if HAS_QT:
        QApplication.processEvents()
    
    mbox.appendPlainText("Attempting SSH connection...")
    if HAS_QT:
        QApplication.processEvents()
    
    # ssh: 1. open connection
    try:
        con.open(user, host, pkey, totp_handler=totp_handler if totp_callback else None)
        # Keep GUI responsive after connection
        if HAS_QT:
            QApplication.processEvents()
    except paramiko.ssh_exception.AuthenticationException as err:
        msg  = '<span style="color:darkred">'
        msg += 'AuthenticationException'
        msg += '</span><br>'
        msg += 'Possible causes:<br>'
        msg += '• Public key not in server\'s ~/.ssh/authorized_keys<br>'
        msg += '• Username is incorrect<br>'
        msg += '• Wrong passphrase (if prompted)<br>'
        msg += '• Key permissions on server (authorized_keys should be 600)<br>'
        msg += '<br><b>Error details:</b> ' + str(err)
        mbox.appendHtml(msg)
        return -1
    except paramiko.ssh_exception.SSHException as err:
        err_str = str(err)
        msg  = '<span style="color:darkred">'
        msg += 'SSHException'
        msg += '</span><br>'
        msg += 'Possible causes:<br>'
        msg += '• Private key file format is invalid<br>'
        msg += '• Key is encrypted and passphrase not provided<br>'
        msg += '• Wrong key type selected<br>'
        msg += '• File permissions issue (key should be readable)<br>'
        msg += '<br><b>Error details:</b><br>'
        # Handle multi-line error messages
        msg += err_str.replace('\n', '<br>')
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

def get_available_gpu_num(con, settings, queue, mbox):
    if not queue:
        return None

    udir = settings.utilities_dir()

    cst_job_submit = pathlib.PurePosixPath(udir).joinpath('cst_job_submit')
    cst_job_submit = '"' + str(cst_job_submit) + '"'
    cst_job_submit += ' --get-available-gpu-num '
    cst_job_submit += ' -q ' + '"' + queue + '"'

    try:
        ret = con.run(cst_job_submit)
        if ret.stderr:
            mbox.appendPlainText(ret.stderr)
            return None
    except Exception as err:
        mbox.appendPlainText(str(err))
        return None

    output = ret.stdout.strip()
    if not output:
        return None

    try:
        return int(output)
    except ValueError:
        mbox.appendPlainText('Unexpected GPU query output: ' + output)
        return None

def main():
    submit()

if __name__ == "__main__":
    main()
