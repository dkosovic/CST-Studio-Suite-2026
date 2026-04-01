import qtpy

from qtpy.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QInputDialog,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStatusBar,
    QSpacerItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
    QAction)

from qtpy.QtGui import QFont, QIcon, QRegularExpressionValidator
from qtpy import QtCore
from qtpy.QtCore import QThread, Signal, QRegularExpression

from . import hpc_cluster_settings
from . import hpc_helper
from . import hpc_job_submission_lib
from . import hpc_json
from . import hpc_submit
from . import observer
import os
import pathlib
import sys
import webbrowser

def get_icon(icon):
    this_dir = pathlib.Path(__file__).parent
    return str(this_dir / "icons" / icon)

class StatusBar():
    def __init__(self, parent):
        self.q_status_bar = QStatusBar(parent)

        self.button = QPushButton('SSH connection: offline')
        self.button.setStyleSheet("QPushButton { border: none; color: darkRed; } QPushButton::hover { color: #478bc4; }")
        self.q_status_bar.addPermanentWidget(self.button)

    def click_connect(self, func):
        self.button.clicked.connect(func)

    def update(self, key, val):
        if key == 'conn':
            if val:
                self.button.setStyleSheet("QPushButton { border: none; color: green; } QPushButton::hover { color: #478bc4; }")
                self.button.setText('SSH connection: online')
                return

            self.button.setStyleSheet("QPushButton { border: none; color: darkRed; } QPushButton::hover { color: #478bc4; }")
            self.button.setText('SSH connection: offline')

        if key == 'save':
            self.q_status_bar.showMessage('saved', val)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HPC Job Submission FE")

        self.my_status_bar = StatusBar(self)
        self.setStatusBar(self.my_status_bar.q_status_bar)

        font = self.font()
        font.setPixelSize(13)
        self.setFont(font)

    def show(self):
        show_task_bar_icon_on_windows()
        super().show()
        self.setFixedHeight(self.height()) # disable vertical resizing
        self.setFixedWidth(self.width()) # disable horizontal resizing

################################################################################
## GroupBoxFile

class GroupBoxFile(QGroupBox):
    def __init__(self, parent, app, message_box):
        QGroupBox.__init__(self)

        self.app = app
        self.message_box = message_box
        self.sim_props = hpc_job_submission_lib.SimPropsCST()

        self.filename = QLineEdit(parent)
        self.filename.setReadOnly(True)
        self.button_open = QPushButton('...', parent)
        self.button_open.setMaximumWidth(30)
        self.button_open.setToolTip('Open CST Project...')
        self.button_open.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.button_open.clicked.connect(self.open)

        layout_file = QHBoxLayout(parent)
        layout_file.addWidget(self.filename)
        layout_file.addWidget(self.button_open, 0, QtCore.Qt.AlignRight)

        self.default_path = os.path.expanduser('~')

        self.setTitle("CST project")
        self.setLayout(layout_file)
        self.subject = None

    def attach_status_ctrl(self, subject):
        self.subject = subject

    def open(self, ext_filename = ""):
        filename = ext_filename
        if not filename:
            filename = QFileDialog.getOpenFileName(self.button_open, "Open CST Project", self.default_path, "CST project (*.cst);;All Files (*)")[0]
        if filename:
            msg  = '<br><span style="color: #478bc4">'
            msg += 'CST project: ' + filename
            msg += '</span>'
            self.message_box.appendHtml(msg)

            if hpc_job_submission_lib.needs_extraction(filename):
                self.message_box.appendPlainText('')
                self.message_box.appendPlainText("CST project directory will be created.")
                self.message_box.appendPlainText("This can take a few seconds.")
                self.message_box.appendPlainText("Please be patient.")
                self.app.processEvents()
                hpc_job_submission_lib.extract_cst_project(filename)
                self.message_box.appendPlainText("CST project sucessfully extracted.")

            if not self.sim_props.load(filename):
               self.message_box.appendHtml('<span style="color:darkred">Error: Simulation properties could not be created.</span>')
               self.message_box.appendPlainText('Save the CST project ' + os.path.basename(filename) + ' in CST Studio Suite to get a docstore file and reopen this app afterwards.')
               return

            self.default_path = os.path.dirname(filename)
            self.filename.setText(filename)

            if self.sim_props.single_solver_run.active:
                self.message_box.appendPlainText('')
                self.message_box.appendPlainText(self.sim_props.single_solver_run.print())
            if self.sim_props.parameter_sweep_run.active:
                self.message_box.appendPlainText('')
                self.message_box.appendPlainText(self.sim_props.parameter_sweep_run.print())
            if self.sim_props.optimizer_run.active:
                self.message_box.appendPlainText('')
                self.message_box.appendPlainText(self.sim_props.optimizer_run.print())
            if self.sim_props.schematic_tasks_run.active:
                self.message_box.appendPlainText('')
                self.message_box.appendPlainText(self.sim_props.schematic_tasks_run.print())

            app_settings = hpc_json.load()

            self.subject.notify('load', True)
            self.subject.notify('load_configure', self.sim_props)
            self.subject.notify('load_set_json', app_settings)

            self.parent().parent().setWindowTitle(filename + ' - HPC Job Submission')

    def json(self):
        app_settings = {}
        app_settings['filename'] = self.filename.text()
        return app_settings

################################################################################
## GroupBoxScheduler

class GroupBoxScheduler(QGroupBox):
    def __init__(self, parent):
        QGroupBox.__init__(self)

        self.subject = None

        label_scheduler = QLabel(parent)
        label_scheduler.setText("Scheduler:")
        self.scheduler = QLabel(parent)
        self.scheduler.setText("None")
        self.scheduler.setMinimumWidth(100)

        label_queue = QLabel(parent)
        label_queue.setText("Queue:")
        self.combobox_queue = QComboBox(parent)
        self.combobox_queue.currentTextChanged.connect(self.queue_changed)

        layout = QGridLayout(parent)
        layout.addWidget(label_scheduler, 0, 0)
        layout.addWidget(self.scheduler, 0, 1)
        layout.addWidget(label_queue, 1, 0)
        layout.addWidget(self.combobox_queue, 1, 1)

        self.setTitle("Scheduler settings")
        self.setLayout(layout)

    def attach_status_ctrl(self, subject):
        self.subject = subject

    def json(self):
        app_settings = {}
        app_settings['scheduler'] = self.scheduler.text()
        app_settings['queue'] = self.combobox_queue.currentText()
        return app_settings

    def set_json(self, app_settings):
        self.combobox_queue.setCurrentText(app_settings['queue'])

    def queue_changed(self, queue):
        if self.subject and queue:
            self.subject.notify('queue_changed', queue)

    def trigger_gpu_query(self):
        """Manually trigger GPU query for current queue"""
        current_queue = self.combobox_queue.currentText()
        if self.subject and current_queue:
            self.subject.notify('queue_changed', current_queue)

    def update(self, key, val):
        if key == 'conn':
            self.setEnabled(val)
        if key == 'scheduler_name':
            self.scheduler.setText(val)
        if key == 'queue_entries':
            queue = self.combobox_queue.currentText()
            self.combobox_queue.clear()
            self.combobox_queue.addItems(val)
            if queue:
                self.combobox_queue.setCurrentText(queue)

################################################################################
## GroupBoxSimulation

class GroupBoxSimulation(QGroupBox):
    def __init__(self, parent, message_box):
        QGroupBox.__init__(self, parent)

        self.setTitle("Simulation settings")
        self.sim_props = hpc_job_submission_lib.SimPropsCST()
        self.remote_gpu_max = None
        self.remote_walltime = None

        label_run_option = QLabel()
        label_run_option.setText("Run option:")
        self.combobox_run_option = QComboBox()
        self.combobox_run_option.setMinimumWidth(100)
        self.combobox_run_option.addItem("Single Solver")
        self.combobox_run_option.addItem("Parameter Sweep")
        self.combobox_run_option.addItem("Optimizer")
        self.combobox_run_option.addItem("Schematic Tasks")
        self.combobox_run_option.currentTextChanged.connect(self.run_option_changed)

        label_type = QLabel()
        label_type.setText("Type:")
        self.combobox_type = QComboBox()
        self.combobox_type.setMinimumWidth(100)
        self.combobox_type.addItem("Single node")
        self.combobox_type.addItem("MPI")
        self.combobox_type.addItem("DC")
        self.combobox_type.currentTextChanged.connect(self.type_changed)

        label_gpus = QLabel()
        label_gpus.setText("GPUs:")
        self.combobox_gpus = QSpinBox()
        self.combobox_gpus.setMinimumWidth(100)
        self.combobox_gpus.setSpecialValueText("None")
        self.combobox_gpus.setMinimum(0)

        label_cores = QLabel()
        label_cores.setText("Cores:")
        self.spin_box_cores = QSpinBox()
        self.spin_box_cores.setMinimumWidth(100)
        self.spin_box_cores.setSpecialValueText("Auto")
        self.spin_box_cores.setMinimum(0)
        self.spin_box_cores.setMaximum(1024)

        label_memory = QLabel()
        label_memory.setText("Memory:")
        self.combobox_memory = QComboBox()
        self.combobox_memory.setMinimumWidth(100)
        self.combobox_memory.setEditable(True)
        memory_validator = QRegularExpressionValidator(
            QRegularExpression(r'^(Auto|[1-9][0-9]*(?:[mMgGtT]|[mM][bB]|[gG][bB]|[tT][bB])?)$'))
        self.combobox_memory.lineEdit().setValidator(memory_validator)
        self.combobox_memory.addItems(['Auto', '16GB', '32GB', '64GB', '128GB', '256GB', '512GB'])
        self.combobox_memory.setCurrentText('Auto')

        label_walltime = QLabel()
        label_walltime.setText("Walltime:")
        self.combobox_walltime = QComboBox()
        self.combobox_walltime.setMinimumWidth(100)
        self.combobox_walltime.setEditable(True)
        walltime_validator = QRegularExpressionValidator(
            QRegularExpression(r'^(Auto|[0-9]{1,3}:[0-5][0-9])$'))
        self.combobox_walltime.lineEdit().setValidator(walltime_validator)
        self._refresh_walltime_options(prefer_remote=False)

        label_nodes = QLabel()
        label_nodes.setText("Nodes:")
        self.spin_box_nodes = QSpinBox()
        self.spin_box_nodes.setMinimumWidth(100)
        self.spin_box_nodes.setMinimum(1)
        self.spin_box_nodes.setMaximum(1)

        my_spacer_1 = QSpacerItem(10, 10)
        my_spacer_2 = QSpacerItem(10, 10)

        layout = QGridLayout(self)
        layout.addWidget(label_run_option, 0, 0)
        layout.addWidget(self.combobox_run_option, 0, 1)
        layout.addItem(my_spacer_1, 0, 2)
        layout.addWidget(label_type, 0, 3)
        layout.addWidget(self.combobox_type, 0, 4)
        layout.addItem(my_spacer_2, 0, 5)
        layout.addWidget(label_nodes, 0, 6)
        layout.addWidget(self.spin_box_nodes, 0, 7)
        layout.addWidget(label_walltime, 1, 0)
        layout.addWidget(self.combobox_walltime, 1, 1)
        layout.addWidget(label_cores, 1, 3)
        layout.addWidget(self.spin_box_cores, 1, 4)
        layout.addWidget(label_memory, 1, 6)
        layout.addWidget(self.combobox_memory, 1, 7)
        layout.addWidget(label_gpus, 1, 8)
        layout.addWidget(self.combobox_gpus, 1, 9)

        self.god_mode = self.init_god_mode()
        if self.god_mode:
            message_box.appendHtml('Simulation properties override mode: <span style="color:green">enabled</span>')

    def init_god_mode(self):
        env_val = os.environ.get('CST_JSF_GOD_MODE', '0')
        if env_val in ['1', 'on', 'true', 'True']:
            return True
        env_val = os.environ.get('CST_JSF_OVERRIDE', '0')
        if env_val in ['1', 'on', 'true', 'True']:
            return True
        return False

    def run_option_changed(self, run_option):
        if run_option == '':
            return

        if self.god_mode:
            return

        sim_props = self.sim_props.get_sim_props(run_option)

        # configure type
        self.combobox_type.currentTextChanged.disconnect(self.type_changed)

        if sim_props.mpi:
            index = self.combobox_type.findText('MPI')
            if index < 0:
                self.combobox_type.insertItem(1, 'MPI')
        else:
            index = self.combobox_type.findText('MPI')
            if index >= 0:
                self.combobox_type.removeItem(index)

        if sim_props.dc:
            index = self.combobox_type.findText('DC')
            if index < 0:
                self.combobox_type.insertItem(2, 'DC')
        else:
            index = self.combobox_type.findText('DC')
            if index >= 0:
                self.combobox_type.removeItem(index)

        self.combobox_type.currentTextChanged.connect(self.type_changed)
        self.type_changed(self.combobox_type.currentText())

    def type_changed(self, type):
        if type == 'Single node':
            self.spin_box_nodes.setMinimum(1)
            self.spin_box_nodes.setMaximum(1)
        elif type == 'DC':
            self.spin_box_nodes.setMinimum(1)
            self.spin_box_nodes.setMaximum(1024)
        elif type == 'MPI':
            self.spin_box_nodes.setMinimum(1)
            self.spin_box_nodes.setMaximum(1024)

        # configure GPUs based on god_mode, local solver properties, and remote cluster limits
        if self.god_mode:
            # In god mode: ignore local solver limits, but respect remote cluster limits
            max_val = self.remote_gpu_max if self.remote_gpu_max is not None else 16
        else:
            # Normal mode: get local solver GPU capabilities
            run_option = self.combobox_run_option.currentText()
            sim_props = self.sim_props.get_sim_props(run_option)

            use_gpu = True
            if type == 'MPI' and (not sim_props.mpi_gpu):
                use_gpu = False

            if use_gpu and sim_props.multi_gpu:
                local_max = 16
            elif use_gpu and sim_props.gpu:
                local_max = 1
            else:
                local_max = 0

            # Combine local solver limits with remote cluster limits
            if self.remote_gpu_max is not None:
                # If solver doesn't support GPUs but remote does, use remote limit
                if local_max == 0:
                    max_val = self.remote_gpu_max
                else:
                    # Both have limits, use the minimum
                    max_val = min(local_max, self.remote_gpu_max)
            else:
                # No remote limit, use local solver limit
                max_val = local_max

        self.combobox_gpus.setMaximum(max_val)
        if self.combobox_gpus.value() > max_val:
            self.combobox_gpus.setValue(max_val)

    def json(self):
        app_settings = {}
        app_settings['solver'] = self.combobox_run_option.currentText()
        app_settings['type'] = self.combobox_type.currentText()
        app_settings['gpu']  = self.combobox_gpus.value()
        app_settings['core'] = self.spin_box_cores.value()
        app_settings['memory'] = self.combobox_memory.currentText().strip() or 'Auto'
        app_settings['node'] = self.spin_box_nodes.value()
        app_settings['walltime'] = self.combobox_walltime.currentText().strip() or 'Auto'
        return app_settings

    def set_json(self, app_settings):
        self.combobox_run_option.setCurrentText(app_settings['solver'])
        self.combobox_type.setCurrentText(app_settings['type'])
        self.combobox_gpus.setValue(app_settings['gpu'])
        self.spin_box_cores.setValue(app_settings['core'])
        memory = app_settings.get('memory', 'Auto')
        self.combobox_memory.setCurrentText(memory if memory else 'Auto')
        self.spin_box_nodes.setValue(app_settings['node'])
        walltime = app_settings.get('walltime', 'Auto')
        self.combobox_walltime.setCurrentText(walltime if walltime else 'Auto')

    def update(self, key, val):
        if key == 'load':
            self.setEnabled(val)
        if key == 'load_configure':
            self.configure(val)
        if key == 'load_set_json':
            self.set_json(val)
        if key == 'queue_changed':
            self.remote_walltime = None
            self._refresh_walltime_options(prefer_remote=False, reset=True)
        if key == 'remote_gpu_max':
            self.remote_gpu_max = val
            self.type_changed(self.combobox_type.currentText())
        if key == 'remote_walltime':
            self.remote_walltime = val
            self._refresh_walltime_options(prefer_remote=True)

    def _refresh_walltime_options(self, prefer_remote=False, reset=False):
        current_val = self.combobox_walltime.currentText().strip() if hasattr(self, 'combobox_walltime') else 'Auto'
        if not current_val or reset:
            current_val = 'Auto'

        target_val = current_val
        if prefer_remote and self.remote_walltime and current_val.lower() in ['auto', 'none']:
            target_val = self.remote_walltime

        self.combobox_walltime.blockSignals(True)
        self.combobox_walltime.clear()
        self.combobox_walltime.addItem('Auto')
        if self.remote_walltime and self.combobox_walltime.findText(self.remote_walltime) < 0:
            self.combobox_walltime.addItem(self.remote_walltime)
        if target_val and target_val != 'Auto' and self.combobox_walltime.findText(target_val) < 0:
            self.combobox_walltime.addItem(target_val)
        self.combobox_walltime.setCurrentText(target_val if target_val else 'Auto')
        self.combobox_walltime.blockSignals(False)

    def configure(self, sim_props):
        self.sim_props = sim_props

        self.combobox_run_option.clear()
        if self.god_mode or sim_props.single_solver_run.active:
            self.combobox_run_option.addItem(sim_props.single_solver_run.label)
        if self.god_mode or sim_props.parameter_sweep_run.active:
            self.combobox_run_option.addItem(sim_props.parameter_sweep_run.label)
        if self.god_mode or sim_props.optimizer_run.active:
            self.combobox_run_option.addItem(sim_props.optimizer_run.label)
        if self.god_mode or sim_props.schematic_tasks_run.active:
            self.combobox_run_option.addItem(sim_props.schematic_tasks_run.label)

################################################################################
## class ClusterSettingsDlg

class ClusterSettingsDlg():
    def __init__(self, parent, my_connect):
        self.my_connect = my_connect
        self.my_dialog_box = hpc_cluster_settings.DialogBox(parent)

    def open(self):
        self.my_dialog_box.open()
        self.my_dialog_box.setFixedHeight(self.my_dialog_box.height()) # disable vertical resizing
        ret = self.my_dialog_box.exec()
        if ret:
            my_json = self.my_dialog_box.json()
            self.my_connect.open(my_json)

    def json(self):
        return self.my_dialog_box.json()

    def set_json(self, app_settings):
       self.my_dialog_box.set_json(app_settings)

def show_task_bar_icon_on_windows():
    if os.name == 'nt':
        import ctypes
        myappid = 'DS.HPC-Job-Submission.2026'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

################################################################################
## SSH Connection Worker Thread

class SSHConnectionWorker(QThread):
    """Worker thread for SSH connection to prevent GUI freezing"""
    finished = Signal(int)  # Signal with return code
    message = Signal(str, str)  # Signal with message type and content
    request_passphrase = Signal(str, object)  # Signal to request passphrase (prompt, result_container)
    
    def __init__(self, con, settings, parent_widget):
        super().__init__()
        self.con = con
        self.settings = settings
        self.parent_widget = parent_widget
        
    def run(self):
        """Run SSH connection in background thread"""
        class ThreadSafeMessageBox:
            """Wrapper to emit messages as signals instead of direct GUI updates"""
            def __init__(self, worker):
                self.worker = worker
                
            def appendPlainText(self, text):
                self.worker.message.emit('plain', text)
                
            def appendHtml(self, text):
                self.worker.message.emit('html', text)
        
        # Wrapper for passphrase callback that works across threads
        def thread_safe_passphrase_callback(prompt_text):
            result_container = {'value': None, 'ready': False}
            
            # Emit signal to main thread and wait
            self.request_passphrase.emit(prompt_text, result_container)
            
            # Wait for result (with timeout)
            timeout_counter = 0
            while not result_container.get('ready', False) and timeout_counter < 3000:  # 30 second timeout
                self.msleep(10)  # Sleep 10ms
                timeout_counter += 1
            
            return result_container.get('value', None)
        
        mbox = ThreadSafeMessageBox(self)
        ret = hpc_submit.open_connection(
            self.con,
            self.settings,
            mbox,
            totp_callback=thread_safe_passphrase_callback
        )
        self.finished.emit(ret)

################################################################################
## class MyConnect

class MyConnect():
    def __init__(self):
        self.message_box = None
        self.con = hpc_submit.Connection()
        self.parent = None
        self.worker_thread = None
        self.settings_json = None

    def init(self, message_box, subject, parent=None):
        self.message_box = message_box
        self.subject = subject
        self.parent = parent

    def _prompt_passcode(self, prompt_text):
        """Show passphrase dialog - must be called from main thread"""
        dialog_parent = self.parent or QApplication.activeWindow()
        
        passcode, ok = QInputDialog.getText(
            dialog_parent,
            "SSH Authentication",
            prompt_text,
            QLineEdit.Password
        )
        
        if not ok:
            return None
        return passcode

    def open(self, settings_json):
        self.settings_json = settings_json

        # Clean up any existing worker thread
        if self.worker_thread is not None and self.worker_thread.isRunning():
            self.worker_thread.quit()
            self.worker_thread.wait()
        
        settings = hpc_submit.Settings(settings_json)
        
        # Create worker thread for SSH connection
        self.worker_thread = SSHConnectionWorker(
            self.con,
            settings,
            self.parent
        )
        
        # Connect signals
        self.worker_thread.message.connect(self._handle_message)
        self.worker_thread.request_passphrase.connect(self._handle_passphrase_request)
        self.worker_thread.finished.connect(lambda ret: self._connection_finished(ret, settings))
        
        # Show connecting message
        self.message_box.appendPlainText("Connecting to SSH...")
        
        # Start worker thread
        self.worker_thread.start()
    
    def _handle_message(self, msg_type, content):
        """Handle messages from worker thread"""
        if msg_type == 'plain':
            self.message_box.appendPlainText(content)
        elif msg_type == 'html':
            self.message_box.appendHtml(content)
    
    def _handle_passphrase_request(self, prompt_text, result_container):
        """Handle passphrase request from worker thread - runs on main thread"""
        # Get passphrase from user
        passphrase = self._prompt_passcode(prompt_text)
        
        # Store result and mark as ready
        result_container['value'] = passphrase
        result_container['ready'] = True
    
    def _connection_finished(self, ret, settings):
        """Handle connection completion"""
        if not ret == 0:
            self.message_box.appendHtml('SSH connection: <span style="color:darkred">failed</span>')
            self.subject.notify('conn', False)
            return

        self.message_box.appendHtml('SSH connection: <span style="color:green">success</span>')
        self.subject.notify('conn', True)

        queue_entries = hpc_submit.get_queues(self.con, settings, self.message_box)
        queue_entries = queue_entries.strip()
        queue_entries = queue_entries.split(':')
        self.subject.notify('queue_entries', queue_entries)

        scheduler_name = hpc_submit.get_scheduler(self.con, settings, self.message_box)
        scheduler_name = scheduler_name.strip()
        self.subject.notify('scheduler_name', scheduler_name)

    def submit(self, settings_json):
        try:
            settings = hpc_submit.Settings(settings_json)
            ret = hpc_submit.submit(self.con, settings, self.message_box)
        except Exception as err:
            self.message_box.appendPlainText(f'Submit exception: {err}')
            ret = -1
        if ret != 0:
            self.message_box.appendPlainText('\nSubmission failed — check the details above and verify cluster configuration and CST project settings.')

    def update(self, key, val):
        if key == 'queue_changed':
            self._update_remote_queue_limits(val)

    def _update_remote_queue_limits(self, queue):
        if not queue:
            self.message_box.appendPlainText('Queue limits query skipped: no queue selected')
            return
        if self.con is None or self.con.con is None:
            self.message_box.appendPlainText('Queue limits query skipped: not connected')
            return

        settings_json = self.settings_json or hpc_json.load()
        if not settings_json:
            self.message_box.appendPlainText('Queue limits query skipped: no settings')
            return

        try:
            self.message_box.appendPlainText(f'Querying queue limits for: {queue}')
            settings = hpc_submit.Settings(settings_json)

            gpu_max = hpc_submit.get_available_gpu_num(self.con, settings, queue, self.message_box)
            if gpu_max is None:
                self.message_box.appendPlainText(f'Remote max GPUs not available for queue: {queue}')
            else:
                self.message_box.appendPlainText(f'Remote max GPUs for queue "{queue}": {gpu_max}')
            self.subject.notify('remote_gpu_max', gpu_max)

            walltime = hpc_submit.get_available_walltime(self.con, settings, queue, self.message_box)
            if walltime is None:
                self.message_box.appendPlainText(f'Remote walltime for queue "{queue}": cluster default')
            else:
                self.message_box.appendPlainText(f'Remote max walltime for queue "{queue}": {walltime}')
            self.subject.notify('remote_walltime', walltime)
        except Exception as err:
            self.message_box.appendPlainText(f'Queue limits query error: {str(err)}')

################################################################################
## QAction classes to handle status control

class QActionSubmit(QAction):
    def __init__(self, icon, label):
        QAction.__init__(self, icon, label)
        self.conn = False
        self.load = False

    def update(self, key, val):
        if key == 'conn':
            self.conn = val
            self.setEnabled(self.conn and self.load)
        if key == 'load':
            self.load = val
            self.setEnabled(self.conn and self.load)

################################################################################
## main function

def main():
    print("Load Qt module: " + qtpy.API_NAME)
    if qtpy.API_NAME == 'PySide2':
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    my_connect = MyConnect()

    app = QApplication()
    app.setStyle("fusion")

    icon = QIcon()
    icon.addFile(get_icon('HPCJobSubmission.ico'))
    app.setWindowIcon(icon)

    main_window = MainWindow()
    centralWidget = QWidget(main_window)
    main_window.setCentralWidget(centralWidget)

    message_box = hpc_helper.MyPlainTextEdit(centralWidget)
    message_box.setReadOnly(True)
    message_box.setMinimumHeight(250)

    my_group_box_file = GroupBoxFile(centralWidget, app, message_box)
    my_group_box_scheduler = GroupBoxScheduler(centralWidget)
    my_group_box_simulation = GroupBoxSimulation(centralWidget, message_box)

    layout = QGridLayout(centralWidget)
    layout.addWidget(my_group_box_file, 0, 0, 1, 2)
    layout.addWidget(my_group_box_simulation, 1, 0, 1, 1)
    layout.addWidget(my_group_box_scheduler, 1, 1, 1, 1)
    layout.addWidget(message_box, 2, 0, 1, 2)
    centralWidget.setLayout(layout)

    myClusterSettingsDlgTest = ClusterSettingsDlg(main_window, my_connect)
    main_window.my_status_bar.click_connect(myClusterSettingsDlgTest.open)

    my_group_box_scheduler.setEnabled(False)
    my_group_box_simulation.setEnabled(False)

    subject = observer.Subject()
    subject.attach(my_group_box_scheduler)
    subject.attach(my_group_box_simulation)
    subject.attach(main_window.my_status_bar)
    subject.attach(message_box)
    subject.attach(my_connect)

    my_group_box_file.attach_status_ctrl(subject)
    my_group_box_scheduler.attach_status_ctrl(subject)

    ################################################################################
    ## toolbar

    def save():
        test = {}
        test = {**test, **my_group_box_file.json()}
        test = {**test, **my_group_box_scheduler.json()}
        test = {**test, **my_group_box_simulation.json()}
        test = {**test, **myClusterSettingsDlgTest.json()}
        hpc_json.save(test)
        subject.notify('save', 2000)

    def submit():
        save()
        my_connect.submit(hpc_json.load())

    def help():
        help_file = pathlib.Path(__file__).parent
        help_file = str(help_file / 'help' / 'main.html')
        webbrowser.open(help_file)

    def click_toolbar():
        print("Click")

    # open
    icon = QIcon(get_icon("open32.png"))
    action_open = QAction(icon, 'Open CST Project...')
    action_open.setToolTip('Open CST Project...')
    action_open.setShortcut("Ctrl+O")
    action_open.triggered.connect(my_group_box_file.open)

    # save
    icon = QIcon(get_icon("save32.png"))
    action_save = QAction(icon, 'Save Settings')
    action_save.setShortcut("Ctrl+S")
    action_save.triggered.connect(save)

    # exit
    icon = QIcon(get_icon("exit32.png"))
    action_exit = QAction(icon, 'Exit')
    action_exit.setShortcut("Alt+F4")
    action_exit.triggered.connect(main_window.close)

    # cluster
    icon = QIcon(get_icon("settings_ssh_32.png"))
    action_cluster = QAction(icon, "Cluster Settings...")
    action_cluster.setToolTip('Cluster Settings...')
    action_cluster.setShortcut("F2")
    action_cluster.triggered.connect(myClusterSettingsDlgTest.open)

    # scheduler
    # icon = QIcon(get_icon("scheduler32.png"))
    # action_scheduler = QAction(icon, "Scheduler Settings...")
    # action_scheduler.setShortcut("F8")
    # action_scheduler.triggered.connect(click_toolbar)

    # submit
    icon = QIcon(get_icon("submit32.png"))
    action_submit = QActionSubmit(icon, "Submit")
    action_submit.setShortcut("F5")
    action_submit.triggered.connect(submit)
    action_submit.setDisabled(True)
    subject.attach(action_submit)

    # help
    icon = QIcon(get_icon("help32.png"))
    action_help = QAction(icon, "Help")
    action_help.setShortcut("F1")
    action_help.triggered.connect(help)

    myToolbar = QToolBar("My main toolbar")
    #myToolbar.setIconSize(QSize(32, 32))
    main_window.addToolBar(myToolbar)

    myToolbar.addAction(action_open)
    myToolbar.addAction(action_save)
    myToolbar.addSeparator()
    myToolbar.addAction(action_cluster)
    # myToolbar.addAction(action_scheduler)
    myToolbar.addSeparator()
    myToolbar.addAction(action_submit)

    ################################################################################
    ## menu

    menu_bar = main_window.menuBar()

    menu_file = menu_bar.addMenu('&File')
    menu_file.addAction(action_open)
    menu_file.addAction(action_save)
    menu_file.addSeparator()
    menu_file.addAction(action_exit)

    menu_settings = menu_bar.addMenu('&Settings')
    menu_settings.addAction(action_cluster)
    # menu_settings.addAction(action_scheduler)

    menu_job = menu_bar.addMenu('&Job')
    menu_job.addAction(action_submit)

    menu_help = menu_bar.addMenu('&Help')
    menu_help.addAction(action_help)

    if not hpc_json.exists():
        save()

    app_settings = hpc_json.load()
    if app_settings:
        myClusterSettingsDlgTest.set_json(app_settings)

    main_window.show()

    my_connect.init(message_box, subject, parent=main_window)
    if app_settings:
        my_connect.open(app_settings)
        my_group_box_scheduler.set_json(app_settings)

    # open file from command line
    if len(sys.argv) > 1:
        cst_project = sys.argv[1]
        cst_project = pathlib.Path(cst_project)
        cst_project = cst_project.as_posix()
        cst_project = str(cst_project)
        if cst_project and os.path.isfile(cst_project) and os.path.splitext(cst_project)[1] == ".cst":
            my_group_box_file.open(cst_project)

    app.exec()

################################################################################
## execute main function

if __name__ == "__main__":
    main()
