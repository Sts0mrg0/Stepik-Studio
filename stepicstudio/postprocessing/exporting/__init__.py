import logging
import os

from django.conf import settings

from stepicstudio.file_system_utils.file_system_client import FileSystemClient
from stepicstudio.models import SubStep, Step, Lesson
from stepicstudio.operations_statuses.operation_result import InternalOperationResult
from stepicstudio.operations_statuses.statuses import ExecutionStatus
from stepicstudio.postprocessing.exporting.video_diff_check import get_diff_times
from stepicstudio.postprocessing.video_synchronization import get_sync_offset
from stepicstudio.utils.extra import translate_non_alphanumerics

PRPROJ_TEMPLATE = 'template.prproj'
PRPROJ_REQUIRED_FILE = 'extendscriptprqe.txt'  # it's required for PPro scripts executing
PRPROJ_PRESET = 'ppro.sqpreset'
PRPROJ_SCRIPT = 'create_deep_structured_project.jsx'
PRPROJ_TEMPLATES_PATH = os.path.join(os.path.dirname(__file__), 'adobe_templates')
PPRO_WIN_PROCESS_NAME = 'Adobe Premiere Pro.exe'

logger = logging.getLogger(__name__)


class PPROCommandBuilder(object):
    """Appends to PPro command ExtendScript code.
    """

    def __init__(self, base_command):
        self.base_command = base_command + ' \"'
        self.script_to_include = ''

    @staticmethod
    def _list_to_array(source_list):
        if not source_list:
            return '[]'

        return '[{}]'.format(', '.join('\'{}\''.format(item) for item in source_list))

    def append_opening_document(self, path: str):
        self.base_command += 'app.openDocument(' + '\'' + path + '\'' + ');'
        return self

    # script including should be appended at the end of command
    # use append_eval_file() if you need to execute detached script
    def append_script_including(self, path: str):
        if self.script_to_include:
            raise Exception('Command already contains script including')

        self.script_to_include = ' //@include \'{}\''.format(path)
        return self

    def append_eval_file(self, path):
        self.base_command += ' $.evalFile(\"{}\");'.format(path)
        return self

    def append_string_const(self, const_name: str, const_value: str):
        self.base_command += ' const {} = \'{}\';'.format(const_name, const_value)
        return self

    def append_const_array(self, const_name: str, const_values: list):
        self.base_command += ' const {} = {};'.format(const_name, self._list_to_array(const_values))
        return self

    def append_bool_value(self, bool_name: str, bool_value: bool):
        self.base_command += ' var {} = Boolean({});'.format(bool_name, str(bool_value).lower())
        return self

    def append_dict(self, name, source_dict, val_type: type = list):
        if val_type is list:
            data = ', '.join('\'{}\': {}'.format(key, self._list_to_array(val)) for key, val in source_dict.items())
        else:
            data = ', '.join('\'{}\': {}'.format(key, val) for key, val in source_dict.items())

        self.base_command += ' var {} = {{{}}};'.format(name, data)
        return self

    def build(self):
        if self.script_to_include:
            self.base_command += self.script_to_include

        return self.base_command + '\"'


def build_ppro_command(base_path, templates_path, screen_files, prof_files, marker_times, sync_offsets, output_name):
    """Builds Premiere Pro script which should be executed through command line.
    Arguments passes to PPro via declaration ExtendScript variable.
    Command includes base script using #include preprocessor directive
    :param marker_times: dict, values are timestamps of frame changes for corresponding screencast
    :param base_path: absolute path to folder which contains target video files;
    :param templates_path: absolute path to .prproj project template and .sqpreset template;
    :param screen_files: list of screencast file names;
    :param prof_files: list of camera recording file names;
    :param output_name: PPro output project name.
    :return PPro command which should be executed using command prompt or shell.
   """

    if not settings.ADOBE_PPRO_PATH:
        raise Exception('Adobe PremierePro configuration is missing. '
                        'Please, specify path to PremierePro in config file.')

    base_command = '\"' + settings.ADOBE_PPRO_PATH + '\" ' + settings.ADOBE_PPRO_CMD
    prproj_template_path = os.path.join(templates_path, PRPROJ_TEMPLATE)
    prproj_preset_path = os.path.join(templates_path, PRPROJ_PRESET)
    script_path = os.path.join(os.path.dirname(__file__), 'adobe_scripts', PRPROJ_SCRIPT)

    if not os.path.isfile(prproj_template_path):
        raise Exception('Template of PremierPro project is missing. '
                        'Please, create empty PPro project at {}'.format(prproj_template_path))

    if not os.path.isfile(prproj_preset_path):
        raise Exception('.sqpreset sequence template file is missing. '
                        'Please, put .sqpreset at {}'.format(prproj_preset_path))

    return PPROCommandBuilder(base_command) \
        .append_opening_document(prproj_template_path.replace(os.sep, '\\\\')) \
        .append_string_const('outputName', output_name) \
        .append_string_const('basePath', base_path.replace(os.sep, '\\\\')) \
        .append_string_const('presetPath', prproj_preset_path.replace(os.sep, '\\\\')) \
        .append_const_array('screenVideos', screen_files) \
        .append_const_array('professorVideos', prof_files) \
        .append_bool_value('needSync', True) \
        .append_dict('markerTimes', marker_times) \
        .append_dict('syncOffsets', sync_offsets, float) \
        .append_script_including(script_path.replace(os.sep, '\\\\')) \
        .build()


def export_obj_to_prproj(db_object, files_extractor) -> InternalOperationResult:
    """Creates PPro project in .prproj format using ExtendScript script.
    Project includes video files of each subitem of corresponding object.
    Screencasts and camera recordings puts on different tracks of single sequence.
    :param files_extractor: function for extracting target filenames from db_object;
    :param db_object: db single object.
    """

    ppro_dir = os.path.dirname(settings.ADOBE_PPRO_PATH)
    if not os.path.isfile(os.path.join(ppro_dir, PRPROJ_REQUIRED_FILE)):
        return InternalOperationResult(ExecutionStatus.FATAL_ERROR,
                                       '\'{0}\' is missing. Please, place \'{0}\' empty file to \n\'{1}\'.'
                                       .format(PRPROJ_REQUIRED_FILE, ppro_dir))

    if FileSystemClient().process_with_name_exists(PPRO_WIN_PROCESS_NAME):
        return InternalOperationResult(ExecutionStatus.FATAL_ERROR,
                                       'Only one instance of PPro may exist. Please, close PPro and try again.')

    screen_files, prof_files, marker_times, sync_offsets = files_extractor(db_object)

    if not screen_files or not prof_files:
        return InternalOperationResult(ExecutionStatus.FATAL_ERROR,
                                       'Object is empty or subitems are broken.')

    try:
        ppro_command = build_ppro_command(db_object.os_path,
                                          PRPROJ_TEMPLATES_PATH,
                                          screen_files,
                                          prof_files,
                                          marker_times,
                                          sync_offsets,
                                          translate_non_alphanumerics(db_object.name))
    except Exception as e:
        return InternalOperationResult(ExecutionStatus.FATAL_ERROR, e)

    exec_status = FileSystemClient().execute_command_sync(ppro_command, allowable_code=1)  # may return 1 - it's OK

    if exec_status.status is not ExecutionStatus.SUCCESS:
        logger.error('Cannot execute PPro command: %s \n PPro command: %s', exec_status.message, ppro_command)
        return InternalOperationResult(ExecutionStatus.FATAL_ERROR,
                                       'Cannot execute PPro command. Check PPro configuration.')

    logger.info('Execution of PPro command started; \n PPro command: %s', ppro_command)
    return InternalOperationResult(ExecutionStatus.SUCCESS)


def get_target_step_files(step_obj):
    screen_files = []
    prof_files = []
    marker_times = {}
    sync_offsets = {}

    for substep in SubStep.objects.filter(from_step=step_obj.id).order_by('start_time'):
        if substep.is_videos_ok and \
                os.path.isfile(substep.os_screencast_path) and \
                os.path.isfile(substep.os_path):
            screen_files.append(substep.screencast_name)
            prof_files.append(substep.camera_recording_name)

            curr_sync_offset = get_sync_offset(substep)
            marker_times[substep.screencast_name] = get_diff_times(substep.os_screencast_path)

            if curr_sync_offset > 0:
                sync_offsets[substep.screencast_name] = curr_sync_offset
            else:
                sync_offsets[substep.camera_recording_name] = abs(curr_sync_offset)

    return screen_files, prof_files, marker_times, sync_offsets


def get_target_lesson_files(lesson_obj):
    screen_files = []
    prof_files = []
    marker_times = {}
    sync_offsets = {}

    if os.path.isdir(lesson_obj.os_path):
        for step in Step.objects.filter(from_lesson=lesson_obj.id).order_by('start_time'):
            step_files = get_target_step_files(step)
            screen_files.extend(step_files[0])
            prof_files.extend(step_files[1])
            marker_times.update(step_files[2])
            sync_offsets.update(step_files[3])

    return screen_files, prof_files, marker_times, sync_offsets


def get_target_course_files(course_obj):
    screen_files = []
    prof_files = []
    marker_times = {}
    sync_offsets = {}

    if os.path.isdir(course_obj.os_path):
        for lesson in Lesson.objects.filter(from_course=course_obj.id).order_by('start_time'):
            lesson_files = get_target_lesson_files(lesson)
            screen_files.extend(lesson_files[0])
            prof_files.extend(lesson_files[1])
            marker_times.update(lesson_files[2])
            sync_offsets.update(lesson_files[3])

    return screen_files, prof_files, marker_times, sync_offsets
