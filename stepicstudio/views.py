from django.shortcuts import render_to_response, render
from django.http import HttpResponseRedirect, Http404, HttpResponse
from django.contrib import auth
from django.contrib.auth.decorators import login_required
from django.core.context_processors import csrf
from django.core.urlresolvers import reverse
from stepicstudio.models import Course, Lesson, Step, SubStep, UserProfile, CameraStatus
from stepicstudio.forms import LessonForm, StepForm
from stepicstudio.VideoRecorder.action import *
from stepicstudio.FileSystemOperations.action import search_as_files_and_update_info, rename_element_on_disk
from stepicstudio.utils.utils import *
import itertools
from django.db.models import Max
from stepicstudio.statistic import add_stat_info
import json
import copy
import requests
from wsgiref.util import FileWrapper
from STEPIC_STUDIO.settings import STATISTIC_URL, SECURE_KEY_FOR_STAT
import logging

logger = logging.getLogger('stepic_studio.views')

def can_edit_page(view_function):
    def process_request(*args, **kwargs):
        access = test_access(args[0].user.id, list(filter(None, args[0].path.split("/"))))
        #logger.debug("runing " + str(args[0].user.id) + str(kwargs['courseId']))
        if not access:
            return HttpResponseRedirect(reverse('stepicstudio.views.login'))
        else:
            #logger.debug(args[0].user.id)
            return view_function(*args, **kwargs)
    return process_request


def cant_edit_course(user_id, course_id):
    courses_from_user_id = Course.objects.all().filter(editors=user_id)
    course = Course.objects.all().filter(id=course_id)[0]
    if course in courses_from_user_id:
        return False
    else:
        return True

##TODO: Add substep checks
def test_access(user_id, path_list):
    if COURSE_ULR_NAME in path_list:
        course_id = path_list[path_list.index(COURSE_ULR_NAME)+1]
    else:
        course_id = None
    if LESSON_URL_NAME in path_list:
        lesson_id = path_list[path_list.index(LESSON_URL_NAME)+1]
    else:
        lesson_id = None
    if STEP_URL_NAME in path_list:
        step_id = path_list[path_list.index(STEP_URL_NAME)+1]
    else:
        step_id = None
    if path_list[0] == COURSE_ULR_NAME and not cant_edit_course(user_id, course_id):
        if lesson_id and not (str(Lesson.objects.all().get(id=lesson_id).from_course) == str(course_id)):
            #logger.debug(lesson_id,  Lesson.objects.all().get(id=lesson_id).from_course, course_id )
            logger.debug("Error here 1 ")
            return False
        if step_id and not (int(Step.objects.all().get(id=step_id).from_lesson) == int(lesson_id)):
            logger.debug("Error here 2 ")
            return False
        return True
    else:

        logger.debug("bla3 ")
        return False



def index(request):
    if request.user.username :
        return HttpResponseRedirect('/loggedin/')
    return HttpResponseRedirect(reverse('stepicstudio.views.login'))


def login(request):
    c = {}
    c.update(csrf(request))
    return render_to_response("login.html", c)


def logout(request):
    auth.logout(request)
    return HttpResponseRedirect(reverse('stepicstudio.views.login'))

@login_required(login_url='/login/')
def get_user_courses(request):
    args = {'full_name': request.user.username, "Courses": Course.objects.all().filter(editors=request.user.id)}
    args.update({"Recording": camera_curr_status})
    return render_to_response("courses.html", args)


@login_required(login_url='/login/')
@can_edit_page
def get_course_page(request, courseId):
    lesson_list = [l for l in Lesson.objects.all().filter(from_course=courseId)]
    lesson_list.sort(key=lambda lesson: lesson.position)
    args = {'full_name': request.user.username, "Course": Course.objects.all().filter(id=courseId)[0],
                                                "Lessons": lesson_list}
    args.update({"Recording": camera_curr_status})
    #logger.debug(UserProfile.objects.get(user=request.user.id).is_ready_to_show_hello_screen)
    return render_to_response("course_view.html", args)


def auth_view(request):
    username = request.POST.get('username', '')
    password = request.POST.get('password', '')
    user = auth.authenticate(username = username, password = password)

    if user is not None:
        say_hello = UserProfile.objects.get(user=user).is_ready_to_show_hello_screen
        auth.login(request, user)
        if say_hello:
            say_hello = '?message=hi'
        else:
            say_hello = ''
        return HttpResponseRedirect("/loggedin/"+say_hello)
    else:
        return HttpResponseRedirect(reverse('stepicstudio.views.login'))


def loggedin(request):
    if request.user.is_authenticated():
        say_hello = bool(request.GET.get('message'))
        return render_to_response("loggedin.html", {'full_name': request.user.username, "Courses": Course.objects.all(), 'say_hello': say_hello})
    else:
        return HttpResponseRedirect(reverse('stepicstudio.views.login'))


##TODO: Implement correctly !!! REDECORATE WITH CAN_EDIT_PAGE
@login_required(login_url='/login/')
def add_lesson(request):
    id = None
    if request.META.get('HTTP_REFERER'):
        url_arr = (request.META.get('HTTP_REFERER')).split('/')
        try:
            id = url_arr[url_arr.index('course') + 1]
        except Exception:
            pass
    if request.POST:
        form = LessonForm(request.POST, userId=request.user.id)
        if form.is_valid():
            from_course = form.data["from_courseName"]
            saved_lesson = form.lesson_save()
            last_saved = Lesson.objects.get(id=saved_lesson.pk)
            last_saved.from_course = from_course
            last_saved.save()
            return HttpResponseRedirect('/course/'+from_course+"/")
    else:
        form = LessonForm(userId=request.user.id, from_course=id)

    args = {"full_name": request.user.username, }
    args.update(csrf(request))
    args.update({"Recording": camera_curr_status})
    args['form'] = form
    return render_to_response("create_lesson.html", args)


@login_required(login_url='/login/')
@can_edit_page
def show_lesson(request, courseId, lessonId):
    args = {"full_name": request.user.username, "Course": Course.objects.all().filter(id=courseId)[0],
                                                       "Lesson": Lesson.objects.all().filter(id=lessonId)[0],
                                                       "Steps": Step.objects.all().filter(from_lesson=lessonId).order_by('position')}
    args.update({"Recording": camera_curr_status})
    return render_to_response("lesson_view.html", args)


@login_required(login_url='/login/')
@can_edit_page
def delete_lesson(request, courseId, lessonId):
    lesson_obj = Lesson.objects.get(id=lessonId)
    redirect_to_course_page = request.path.split("/")
    if not delete_files_associated(redirect_to_course_page):
        raise Exception("Cant delete files")
    for step in Step.objects.all().filter(from_lesson=lessonId):
        for substep in SubStep.objects.all().filter(from_step=step.pk):
            substep.delete()
        step.delete()
    lesson_obj.delete()
    return HttpResponseRedirect('/'+'/'.join(redirect_to_course_page[1:3])+'/')


##IMPLEMENT CORRECTLY

@login_required(login_url='/login/')
@can_edit_page
def add_step(request, courseId, lessonId):
    if request.POST:
        form = StepForm(request.user.id, lessonId, request.POST)
        if form.is_valid():
            from_lesson = form.data["from_lessonId"]
            saved_step = form.step_save()
            last_saved = Step.objects.get(id=saved_step.pk)
            last_saved.from_lesson = from_lesson
            last_saved.save()
            return HttpResponseRedirect('/' + COURSE_ULR_NAME + '/' + courseId + '/' + LESSON_URL_NAME + '/' + from_lesson + '/')
    else:
        form = StepForm(request.user.id, lessonId)

    args = {"full_name": request.user.username, "postUrl": "/" + COURSE_ULR_NAME + "/"+courseId+"/" + LESSON_URL_NAME
                                                                                + "/"+lessonId+"/add_step/"}
    args.update({"Recording": camera_curr_status})
    args.update(csrf(request))
    args['form'] = form
    return render_to_response("create_step.html", args)


def geterate_notes_page(request, course_id):
    lessons = Lesson.objects.all().filter(from_course=course_id)
    notes = list()
    for l in lessons:
        steps = Step.objects.all().filter(from_lesson=l.id).order_by('id')
        for s in steps:
            if s.text_data:
                notes.append({'id': 'Step' + str(s.id) + 'from' + str(s.from_lesson), 'text': s.text_data})
    args = {'notes': notes}
    return render_to_response('notes_page.html', args)

@login_required(login_url='/login/')
@can_edit_page
def show_step(request, courseId, lessonId, stepId):
    step_obj = Step.objects.get(id=stepId)
    if request.POST and request.is_ajax():
        user_action = dict(request.POST.lists())['action'][0]
        if user_action == "start":
            print('OK')
            if start_new_step_recording(request, courseId, lessonId, stepId):
                step_obj.is_fresh = False
                return HttpResponse("Ok")
        elif user_action == "stop":
            if stop_recording(request, courseId, lessonId, stepId):
                return HttpResponse("Ok")
        return Http404
    postURL = "/" + COURSE_ULR_NAME + "/" + courseId + "/" + LESSON_URL_NAME + "/"+lessonId+"/" + STEP_URL_NAME + "/" + stepId + "/"
    all_Substeps = SubStep.objects.all().filter(from_step=stepId)
    summ_time = update_time_records(all_Substeps)
    step_obj.is_fresh = True
    step_obj.duration = summ_time
    step_obj.save()
    args =  {"full_name": request.user.username, "Course": Course.objects.all().get(id=courseId),
                                                     "Lesson": Lesson.objects.all().get(id=lessonId),
                                                     "Step": Step.objects.get(id=stepId),
                                                     "postUrl": postURL,
                                                     "SubSteps": all_Substeps, }
    args.update({"Recording": camera_curr_status})
    args.update(csrf(request))
    return render_to_response("step_view.html", args)

###TODO: request.META is BAD! replace for AJAX requests!
@login_required(login_url='/login')
def notes(request, stepId):
    if request.POST:
        step_obj = Step.objects.get(id=stepId)
        step_obj.text_data = dict(request.POST.lists())['note'][0]
        step_obj.save()
    args = {}
    args.update(csrf(request))
    return HttpResponseRedirect(request.META['HTTP_REFERER'], args)




## TODO: TOKEN at POSTrequest to statistic server is insecure
@login_required(login_url='/login/')
def start_new_step_recording(request, courseId, lessonId, stepId):
    substep = SubStep()
    substep.from_step = stepId
    stepIndex = len(SubStep.objects.all().filter(from_step=stepId)) + 1
    substep.name = "Step"+str(stepIndex)+"from"+str(substep.from_step)
    while SubStep.objects.filter(name=substep.name).count():
        stepIndex += 1
        substep.name = "Step"+str(stepIndex)+"from"+str(substep.from_step)
    substep.save()
    postURL = "/" + COURSE_ULR_NAME + "/" + courseId + "/" + LESSON_URL_NAME + "/"+lessonId+"/" + STEP_URL_NAME + "/" + stepId + "/"
    args = {"full_name": request.user.username, "Course": Course.objects.all().filter(id=courseId)[0],
                                                "postUrl": postURL,
                                                "Lesson": Lesson.objects.all().filter(id=lessonId)[0],
                                                "Step": Step.objects.all().filter(id=stepId)[0],
                                                "SubSteps": SubStep.objects.all().filter(from_step=stepId),
                                                "currSubStep": SubStep.objects.get(id=substep.pk)}
    args.update(csrf(request))
    is_started = start_recording(user_id=request.user.id, user_profile=UserProfile.objects.get(user=request.user.id), data=args)
    if is_started:
        args.update({"Recording": True})
        args.update({"StartTime": CameraStatus.objects.get(id="1").start_time / 1000})
    else:
        return False
    try:
        #logger.debug("sent data to stepic.mehanig.com")
        #data = {'User': request.user.username, 'Name': substep.name, 'Duration': 'No data', 'priority':'1', 'status':'0',
        #        'token': SECURE_KEY_FOR_STAT}
        #r = requests.post(STATISTIC_URL, data=data)
        #logger.debug('STATISTIC STATUS:', r)
        pass
    except Exception as e:
        #logger.debug('Error!!!: ', e)
        pass
    return True

@login_required(login_url='/login')
def recording_page(request, courseId, lessonId, stepId):
    postURL = "/" +  COURSE_ULR_NAME + "/" + courseId + "/" + LESSON_URL_NAME + "/"+lessonId+"/" + STEP_URL_NAME + "/" + stepId + "/"
    args = {"full_name": request.user.username, "Course": Course.objects.all().filter(id=courseId)[0],
                                                "postUrl": postURL,
                                                "Lesson": Lesson.objects.all().filter(id=lessonId)[0],
                                                "Step": Step.objects.all().filter(id=stepId)[0],
                                                "SubSteps": SubStep.objects.all().filter(from_step=stepId), }
    args.update({"Recording": camera_curr_status})
    return render_to_response("step_view.html", args)

##TODO: Add statistic here
@login_required(login_url='/login')
@can_edit_page
def stop_all_recording(request):
        args = {"full_name": request.user.username }
        args.update(csrf(request))
        stop_cam_recording()
        args.update({"Recording": camera_curr_status})
        return render_to_response("courses.html", args)

@login_required(login_url="/login")
def stop_recording(request, courseId, lessonId, stepId):
        postURL = "/" +  COURSE_ULR_NAME + "/" + courseId + "/" + LESSON_URL_NAME + "/"+lessonId+"/" + STEP_URL_NAME + "/" + stepId + "/"
        args = {"full_name": request.user.username, "Course": Course.objects.all().filter(id=courseId)[0],
                "postUrl": postURL, "Lesson": Lesson.objects.all().filter(id=lessonId)[0],
                "Step": Step.objects.all().filter(id=stepId)[0],
                "SubSteps": SubStep.objects.all().filter(from_step=stepId), }
        args.update(csrf(request))
        stop_cam_recording()
        args.update({"Recording": camera_curr_status})
        last_substep_time = SubStep.objects.all().filter(from_step=stepId).aggregate(Max('start_time'))['start_time__max']
        recorded_substep = SubStep.objects.all().filter(start_time=last_substep_time)[0]
        add_stat_info(recorded_substep.id)
        return True




@login_required(login_url='/login/')
@can_edit_page
def remove_substep(request, courseId, lessonId, stepId, substepId):
    substep = SubStep.objects.get(id=substepId)
    postURL = "/" +  COURSE_ULR_NAME + "/" + courseId + "/" + LESSON_URL_NAME + "/"+lessonId+"/" + STEP_URL_NAME + "/" + stepId + "/"
    args = {"full_name": request.user.username,
                                                     "Course": Course.objects.all().filter(id=courseId)[0],
                                                     "Lesson": Lesson.objects.all().filter(id=lessonId)[0],
                                                     "Step": Step.objects.all().filter(id=stepId)[0],
                                                     "postUrl": postURL,
                                                     "SubSteps": SubStep.objects.all().filter(from_step=stepId),
                                                     "currSubStep": substep}

    substep_deleted = delete_substep_files(user_id=request.user.id,
                                           user_profile=UserProfile.objects.get(user=request.user.id), data=args)
    substep.delete()
    return HttpResponseRedirect(postURL)

@login_required(login_url="/login/")
@can_edit_page
def delete_step(request, courseId, lessonId, stepId):
    step = Step.objects.all().get(id=stepId)
    postURL = "/" +  COURSE_ULR_NAME + "/" + courseId + "/" + LESSON_URL_NAME + "/"+lessonId+"/"
    args = {"full_name": request.user.username,
                                                     "Course": Course.objects.all().filter(id=courseId)[0],
                                                     "Lesson": Lesson.objects.all().filter(id=lessonId)[0],
                                                     "Step": Step.objects.all().filter(id=stepId)[0],
                                                     "postUrl": postURL,
                                                     "SubSteps": SubStep.objects.all().filter(from_step=stepId)}
    substeps = SubStep.objects.all().filter(from_step=stepId)
    for substep in substeps:
        substep.delete()
    step_deleted = delete_step_files(user_id=request.user.id,
                                            user_profile=UserProfile.objects.get(user=request.user.id), data=args)
    step.delete()
    return HttpResponseRedirect(postURL)

@login_required(login_url='/login/')
def user_profile(request):
    return render_to_response("UserProfile.html", {"full_name": request.user.username,
                                                  "settings": UserProfile.objects.get(user_id=request.user.id),
                                                  })

##TODO: Refactor
def reorder_elements(request):
    if request.POST and request.is_ajax():
        args = url_to_args(request.META['HTTP_REFERER'])
        args.update({"user_profile": UserProfile.objects.get(user=request.user.id)})
        if request.POST.get('type') == 'lesson' or request.POST.get('type') == 'step':
            neworder = request.POST.getlist('ids[]')
            for i in range(len(neworder)):
                print(neworder)
                id = neworder[i]
                if id == '':
                    break
                if request.POST.get('type') == 'lesson':
                    l = Lesson.objects.get(id=id)
                else:
                    l = Step.objects.get(id=id)
                l.position = i
                l.save()
        files_update(**args)
        return HttpResponse("Ok")

    else:
        return Http404

@login_required(login_url="/login/")
@can_edit_page
def show_course_struct(request, courseId):
    args = {"full_name": request.user.username, "Course": Course.objects.all().get(id=courseId)}
    args.update({"user_profile": UserProfile.objects.get(user=request.user.id)})
    args.update({"Recording": camera_curr_status})
    all_lessons = Lesson.objects.all().filter(from_course=courseId)
    args.update({"all_course_lessons": all_lessons})
    all_steps = ()
    for l in all_lessons:
        steps = Step.objects.all().filter(from_lesson=l.pk)
        all_steps = itertools.chain(all_steps, steps)
    all_steps = list(all_steps)
    args.update({"all_steps": all_steps})
    args = search_as_files_and_update_info(args)
    args.update(csrf(request))
    return render_to_response("course_struct.html", args)

@login_required(login_url="/login/")
@can_edit_page
def view_stat(request, courseId):
    args = {"full_name": request.user.username, "Course": Course.objects.all().get(id=courseId)}
    return render_to_response("stat.html", args)



###TODO: try catch works incorrectly. Should check for file size before return
###TODO: hotfix here is bad
###TODO: This function is unsanfe, its possible to watch other users files
def video_view(request, substepId):
    substep = SubStep.objects.all().get(id=substepId)
    try:
        file = FileWrapper(open(substep.os_path, 'rb'))
        response = HttpResponse(file, content_type='video/TS')
        response['Content-Disposition'] = 'inline; filename='+substep.name+"_"+SUBSTEP_PROFESSOR
        return response
    except Exception as e:
        logger.debug(e)
    try:
        substep = SubStep.objects.all().get(id=substepId)
        path = '/'.join((list(filter(None, substep.os_path.split("/"))))[:-1]) + "/" + str(SUBSTEP_PROFESSOR)[1:]
        file = FileWrapper(open(path, 'rb'))
        logger.debug(path)
        response = HttpResponse(file, content_type='video/TS')
        response['Content-Disposition'] = 'inline; filename='+substep.name+"_"+SUBSTEP_PROFESSOR
        return response
    except Exception as e:
        logger.debug(e)
        return HttpResponse("File to large. Please watch it on server.")



###TODO: hotfix here is bad =(
def video_screen_view(request, substepId):
    substep = SubStep.objects.all().get(id=substepId)
    err = None
    try:
        path = '/'.join((list(filter(None, substep.os_path.split("/"))))[:-1]) + "/" + substep.name + SUBSTEP_SCREEN
        file = FileWrapper(open(path, 'rb'))
        response = HttpResponse(file, content_type='video/mkv')
        response['Content-Disposition'] = 'inline; filename='+substep.name+"_"+SUBSTEP_SCREEN
        return response
    except Exception as e:
        pass
        err = e
    try:
        substep = SubStep.objects.all().get(id=substepId)
        path = '/'.join((list(filter(None, substep.os_path.split("/"))))[:-1]) + "/" + str(SUBSTEP_SCREEN)[1:]
        file = FileWrapper(open(path, 'rb'))
        response = HttpResponse(file, content_type='video/ts')
        response['Content-Disposition'] = 'inline; filename='+substep.name+"_"+SUBSTEP_SCREEN
        return response
    except Exception as e:
        return HttpResponse("File to large. Please watch it on server.")


def rename_elem(request):
    if request.POST and request.is_ajax():
        rest_data = dict(request.POST.lists())
        logger.debug("Rename_elem POST data: %s", rest_data)
        if 'step' in rest_data['type'] or 'lesson' in rest_data['type']:
            if 'step' in rest_data['type']:
                ObjToRename = Step.objects.all().get(id=rest_data['id'][0])
            else:
                ObjToRename = Lesson.objects.all().get(id=rest_data['id'][0])
            logger.debug('Renaming: %s', ObjToRename.os_path)
            TmpStep = copy.copy(ObjToRename)
            TmpStep.name = rest_data['name_new'][0]
            logger.debug('Trying to %s', TmpStep.os_path)
            if not camera_curr_status():
                if rename_element_on_disk(ObjToRename, TmpStep):
                    ObjToRename.delete()
                    TmpStep.save()
                    return HttpResponse("Ok")
                else:
                    return Http404
            else:
                return Http404
        else:
            return Http404
    else:
        return Http404
