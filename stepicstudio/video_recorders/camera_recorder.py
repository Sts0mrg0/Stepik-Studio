import logging
import os
import subprocess

from django.conf import settings
from singleton_decorator import singleton

from stepicstudio.file_system_utils.file_system_client import FileSystemClient
from stepicstudio.const import FFMPEGcommand
from stepicstudio.operations_statuses.operation_result import InternalOperationResult
from stepicstudio.operations_statuses.statuses import ExecutionStatus
from stepicstudio.scheduling.task_manager import TaskManager
from stepicstudio.video_recorders.postprocessable_recorder import PostprocessableRecorder

KILL_DELAY = 3


@singleton
class ServerCameraRecorder(PostprocessableRecorder):
    def __init__(self):
        super().__init__()
        self.__fs_client = FileSystemClient()
        self.__command = FFMPEGcommand
        self.__process = None
        self.__logger = logging.getLogger('stepic_studio.video_recorders.camera_recorder')
        self.last_processed_path = None
        self.last_processed_file = None
        self._load_postprocessing_pipe(settings.SERVER_POSTPROCESSING_PIPE)

    def start_recording(self, path: str, filename: str) -> InternalOperationResult:
        if self.is_active():
            self.__logger.error('Can\'t start FFMPEG for file %s: camera is acctually recording (process with PID %s)',
                                os.path.join(path, filename),
                                self.__process.pid)
            return InternalOperationResult(ExecutionStatus.FATAL_ERROR, 'Camera is actually recording')

        local_command = self.__command + os.path.join(path, filename)
        result, self.__process = self.__fs_client.execute_command(local_command, stdin=subprocess.PIPE)

        if result.status is ExecutionStatus.SUCCESS:
            self.__logger.info('Successfully start camera recording (FFMPEG PID: %s; FFMPEG command: %s)',
                               self.__process.pid, local_command)
            self.last_processed_file = filename
            self.last_processed_path = path
        else:
            self.__logger.error('Camera recording start failed: %s; FFMPEG command: %s', result.message, local_command)

        return result

    def stop_recording(self) -> InternalOperationResult:
        if not self.is_active():
            if self.__process is None:
                pid = None
            else:
                pid = self.__process.pid
                self.__process = None
            self.__logger.warning('Camera isn\'t active: can\'t stop non existing FFMPEG process '
                                  '(try to stop process with PID %s)', pid)
            return InternalOperationResult(ExecutionStatus.SUCCESS,
                                           'Camera isn\'t active: can\'t stop non existing FFMPEG process')

        result = self.__fs_client.send_quit_signal(self.__process)
        TaskManager().run_with_delay(FileSystemClient.kill_process, [self.__process.pid], delay=KILL_DELAY)

        if result.status is ExecutionStatus.SUCCESS:
            self.__logger.info('Successfully stop camera recording (FFMPEG PID: %s)', self.__process.pid)
            self.__process = None
            self._apply_pipe(self.last_processed_path, self.last_processed_file)
            return result
        else:
            self.__logger.error('Problems while stop camera recording (FFMPEG PID: %s) : %s', self.__process.pid,
                                result.message)
            return result

    def is_active(self) -> bool:
        return self.__process is not None and self.__process.poll() is None
