'''
    Author: Guanghan Ning
    E-mail: guanghan.ning@jd.com
    April 23rd, 2019
    LightTrack: A Generic Framework for Online Top-Down Human Pose Tracking
    Demo on videos using YOLOv3 detector and Mobilenetv1-Deconv.
'''
import time
import argparse

# import vision essentials
import cv2

# import Network
from network_mobile_deconv import Network
# pose estimation utils
from HPE.dataset import Preprocessing
from HPE.config import cfg
from lib.tfflat.base import Tester
from lib.nms.gpu_nms import gpu_nms


# import my own utils
import sys, os, time

from visualizer import *
sys.path.append(os.path.abspath("./graph/"))
from utils_json import *
from utils_io_file import *
from utils_io_folder import *

flag_visualize = True
flag_nms = False #Default is False, unless you know what you are doing

os.environ["CUDA_VISIBLE_DEVICES"]="0"


def initialize_parameters():
    global video_name, img_id

    global nms_method, nms_thresh, min_scores, min_box_size
    nms_method = 'nms'
    nms_thresh = 1.
    min_scores = 1e-10
    min_box_size = 0.

    global keyframe_interval, enlarge_scale, pose_matching_threshold
    keyframe_interval = 10 # choice examples: [2, 3, 5, 8, 10]
    enlarge_scale = 0.2
    pose_matching_threshold = 0

    global flag_flip
    flag_flip = True

    global total_time_POSE, total_time_DET, total_time_ALL, total_num_FRAMES, total_num_PERSONS
    total_time_POSE = 0
    total_time_DET = 0
    total_time_ALL = 0
    total_num_FRAMES = 0
    total_num_PERSONS = 0
    return


def light_track(pose_estimator,detector,
                image_folder, output_json_path,
                visualize_folder, detect_folder,output_video_path):

    global total_time_POSE, total_time_DET, total_time_ALL, total_num_FRAMES, total_num_PERSONS
    ''' 1. statistics: get total time for lighttrack processing'''
    st_time_total = time.time()

    # process the frames sequentially

    frame_prev = -1
    bbox_dets_list_list = []
    keypoints_list_list = []

    flag_mandatory_keyframe = False
    img_id = -1
    img_paths = get_immediate_childfile_paths(image_folder)
    num_imgs = len(img_paths)
    total_num_FRAMES = num_imgs
    if detector == 'Centernet':
        from detector.CenterNet import CtdetDetector
        from detector.config.opts import opts
        opt = opts().init()
        centernet = CtdetDetector(opt)
    elif detector == 'yolo':
        from detector.detector_yolov3 import inference_yolov3

    while img_id < num_imgs-1:
        img_id += 1
        img_path = img_paths[img_id]
        print("Current tracking: [image_id:{}]".format(img_id))

        frame_cur = img_id
        if (frame_cur == frame_prev):
            frame_prev -= 1

        ''' KEYFRAME: loading results from other modules '''
        # if is_keyframe(img_id, keyframe_interval) or flag_mandatory_keyframe:
        bbox_dets_list = []  # keyframe: start from empty
        keypoints_list = []  # keyframe: start from empty

        # perform detection at keyframes
        st_time_detection = time.time()
        image = cv2.imread(img_path)
        if detector == 'Centernet':
            human_candidates = centernet.run(image)
        elif detector == 'yolo':
            human_candidates=inference_yolov3(img_path)




        end_time_detection = time.time()
        total_time_DET += (end_time_detection - st_time_detection)

        num_dets = len(human_candidates)
        print("Keyframe: {} detections".format(num_dets))

        # if nothing detected at keyframe, regard next frame as keyframe because there is nothing to track
        if num_dets <= 0:
            # add empty result
            bbox_det_dict = {"img_id":img_id,
                             "det_id":  0,
                             "imgpath": img_path,
                             "bbox": [0, 0, 2, 2]}
            bbox_dets_list.append(bbox_det_dict)

            keypoints_dict = {"img_id":img_id,
                              "det_id": 0,
                              "imgpath": img_path,
                              "keypoints": []}
            keypoints_list.append(keypoints_dict)

            bbox_dets_list_list.append(bbox_dets_list)
            keypoints_list_list.append(keypoints_list)

            continue

        ''' 2. statistics: get total number of detected persons '''
        total_num_PERSONS += num_dets

        for det_id in range(num_dets):
            # obtain bbox position
            bbox_gt = human_candidates[det_id]

            # enlarge bbox by 20% with same center position
            bbox_x1y1x2y2 = xywh_to_x1y1x2y2(bbox_gt)
            bbox_in_xywh = enlarge_bbox(bbox_x1y1x2y2, enlarge_scale)
            bbox_gt = x1y1x2y2_to_xywh(bbox_in_xywh)

            # Keyframe: use provided bbox
            bbox_det = bbox_gt
            if bbox_det[2] <= 0 or bbox_det[3] <= 0 or bbox_det[2] > 2000 or bbox_det[3] > 2000:
                bbox_det = [0, 0, 2, 2]
                track_id = None  # this id means null
                continue

            # update current frame bbox
            bbox_det_dict = {"img_id":img_id,
                             "det_id":det_id,
                             "imgpath": img_path,
                             "bbox":bbox_det}
            # obtain keypoints for each bbox position in the keyframe
            st_time_pose = time.time()
            keypoints = inference_keypoints(pose_estimator, bbox_det_dict)[0]["keypoints"]
            end_time_pose = time.time()
            total_time_POSE += (end_time_pose - st_time_pose)
            bbox_det_dict = {"img_id":img_id,
                             "det_id":det_id,
                             "imgpath": img_path,
                             "bbox":bbox_det}
            bbox_dets_list.append(bbox_det_dict)

            # update current frame keypoints
            keypoints_dict = {"img_id":img_id,
                              "det_id":det_id,
                              "imgpath": img_path,
                              "keypoints":keypoints}
            keypoints_list.append(keypoints_dict)

        # update frame
        bbox_dets_list_list.append(bbox_dets_list)
        keypoints_list_list.append(keypoints_list)
        frame_prev = frame_cur


    # ''' 1. statistics: get total time for lighttrack processing'''
    end_time_total = time.time()
    total_time_ALL += (end_time_total - st_time_total)

    # convert results into openSVAI format
    print("Exporting Results in openSVAI Standard Json Format...")
    poses_standard = pose_to_standard_mot(keypoints_list_list, bbox_dets_list_list)


    # output json file
    pose_json_folder, _ = get_parent_folder_from_path(output_json_path)
    create_folder(pose_json_folder)
    write_json_to_file(poses_standard, output_json_path)
    print("Json Export Finished!")

    # visualization
    if flag_visualize is True:
        print("Visualizing Pose Tracking Results...")
        create_folder(visualize_folder)
        show_all_from_standard_json(output_json_path, classes, joint_pairs, joint_names, image_folder, visualize_folder, flag_track = True)
        print("Visualization Finished!")

        img_paths = get_immediate_childfile_paths(visualize_folder)
        avg_fps = total_num_FRAMES / total_time_ALL
        make_video_from_images(img_paths, output_video_path, fps=avg_fps, size=None, is_color=True, format="XVID")






def iou(boxA, boxB):
    # box: (x1, y1, x2, y2)
    # determine the (x, y)-coordinates of the intersection rectangle
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])

    # compute the area of intersection rectangle
    interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)

    # compute the area of both the prediction and ground-truth
    # rectangles
    boxAArea = (boxA[2] - boxA[0] + 1) * (boxA[3] - boxA[1] + 1)
    boxBArea = (boxB[2] - boxB[0] + 1) * (boxB[3] - boxB[1] + 1)

    # compute the intersection over union by taking the intersection
    # area and dividing it by the sum of prediction + ground-truth
    # areas - the interesection area
    iou = interArea / float(boxAArea + boxBArea - interArea)

    # return the intersection over union value
    return iou


def get_bbox_from_keypoints(keypoints_python_data):
    if keypoints_python_data == [] or keypoints_python_data == 45*[0]:
        return [0, 0, 2, 2]

    num_keypoints = len(keypoints_python_data)
    x_list = []
    y_list = []
    for keypoint_id in range(int(num_keypoints / 3)):
        x = keypoints_python_data[3 * keypoint_id]
        y = keypoints_python_data[3 * keypoint_id + 1]
        vis = keypoints_python_data[3 * keypoint_id + 2]
        if vis != 0 and vis!= 3:
            x_list.append(x)
            y_list.append(y)
    min_x = min(x_list)
    min_y = min(y_list)
    max_x = max(x_list)
    max_y = max(y_list)

    if not x_list or not y_list:
        return [0, 0, 2, 2]

    scale = enlarge_scale # enlarge bbox by 20% with same center position
    bbox = enlarge_bbox([min_x, min_y, max_x, max_y], scale)
    bbox_in_xywh = x1y1x2y2_to_xywh(bbox)
    return bbox_in_xywh


def enlarge_bbox(bbox, scale):
    assert(scale > 0)
    min_x, min_y, max_x, max_y = bbox
    margin_x = int(0.5 * scale * (max_x - min_x))
    margin_y = int(0.5 * scale * (max_y - min_y))
    if margin_x < 0: margin_x = 2
    if margin_y < 0: margin_y = 2

    min_x -= margin_x
    max_x += margin_x
    min_y -= margin_y
    max_y += margin_y

    width = max_x - min_x
    height = max_y - min_y
    if max_y < 0 or max_x < 0 or width <= 0 or height <= 0 or width > 2000 or height > 2000:
        min_x=0
        max_x=2
        min_y=0
        max_y=2

    bbox_enlarged = [min_x, min_y, max_x, max_y]
    return bbox_enlarged


def inference_keypoints(pose_estimator, test_data):
    cls_dets = test_data["bbox"]
    # nms on the bboxes
    if flag_nms is True:
        cls_dets, keep = apply_nms(cls_dets, nms_method, nms_thresh)
        test_data = np.asarray(test_data)[keep]
        if len(keep) == 0:
            return -1
    else:
        test_data = [test_data]

    # crop and detect pose
    pose_heatmaps, details, cls_skeleton, crops, start_id, end_id = get_pose_from_bbox(pose_estimator, test_data, cfg)
    # get keypoint positions from pose
    keypoints = get_keypoints_from_pose(pose_heatmaps, details, cls_skeleton, crops, start_id, end_id)
    # dump results
    results = prepare_results(test_data[0], keypoints, cls_dets)
    return results


def apply_nms(cls_dets, nms_method, nms_thresh):
    # nms and filter
    keep = np.where((cls_dets[:, 4] >= min_scores) &
                    ((cls_dets[:, 3] - cls_dets[:, 1]) * (cls_dets[:, 2] - cls_dets[:, 0]) >= min_box_size))[0]
    cls_dets = cls_dets[keep]
    if len(cls_dets) > 0:
        if nms_method == 'nms':
            keep = gpu_nms(cls_dets, nms_thresh)
        elif nms_method == 'soft':
            keep = cpu_soft_nms(np.ascontiguousarray(cls_dets, dtype=np.float32), method=2)
        else:
            assert False
    cls_dets = cls_dets[keep]
    return cls_dets, keep


def get_pose_from_bbox(pose_estimator, test_data, cfg):
    cls_skeleton = np.zeros((len(test_data), cfg.nr_skeleton, 3))
    crops = np.zeros((len(test_data), 4))

    batch_size = 1
    start_id = 0
    end_id = min(len(test_data), batch_size)

    test_imgs = []
    details = []
    for i in range(start_id, end_id):
        test_img, detail = Preprocessing(test_data[i], stage='test')
        test_imgs.append(test_img)
        details.append(detail)

    details = np.asarray(details)
    feed = test_imgs
    for i in range(end_id - start_id):
        ori_img = test_imgs[i][0].transpose(1, 2, 0)
        if flag_flip == True:
            flip_img = cv2.flip(ori_img, 1)
            feed.append(flip_img.transpose(2, 0, 1)[np.newaxis, ...])
    feed = np.vstack(feed)

    res = pose_estimator.predict_one([feed.transpose(0, 2, 3, 1).astype(np.float32)])[0]
    res = res.transpose(0, 3, 1, 2)

    if flag_flip == True:
        for i in range(end_id - start_id):
            fmp = res[end_id - start_id + i].transpose((1, 2, 0))
            fmp = cv2.flip(fmp, 1)
            fmp = list(fmp.transpose((2, 0, 1)))
            for (q, w) in cfg.symmetry:
                fmp[q], fmp[w] = fmp[w], fmp[q]
            fmp = np.array(fmp)
            res[i] += fmp
            res[i] /= 2

    pose_heatmaps = res
    return pose_heatmaps, details, cls_skeleton, crops, start_id, end_id


def get_keypoints_from_pose(pose_heatmaps, details, cls_skeleton, crops, start_id, end_id):
    res = pose_heatmaps
    for test_image_id in range(start_id, end_id):
        r0 = res[test_image_id - start_id].copy()
        r0 /= 255.
        r0 += 0.5

        for w in range(cfg.nr_skeleton):
            res[test_image_id - start_id, w] /= np.amax(res[test_image_id - start_id, w])

        border = 10
        dr = np.zeros((cfg.nr_skeleton, cfg.output_shape[0] + 2 * border, cfg.output_shape[1] + 2 * border))
        dr[:, border:-border, border:-border] = res[test_image_id - start_id][:cfg.nr_skeleton].copy()

        for w in range(cfg.nr_skeleton):
            dr[w] = cv2.GaussianBlur(dr[w], (21, 21), 0)

        for w in range(cfg.nr_skeleton):
            lb = dr[w].argmax()
            y, x = np.unravel_index(lb, dr[w].shape)
            dr[w, y, x] = 0
            lb = dr[w].argmax()
            py, px = np.unravel_index(lb, dr[w].shape)
            y -= border
            x -= border
            py -= border + y
            px -= border + x
            ln = (px ** 2 + py ** 2) ** 0.5
            delta = 0.25
            if ln > 1e-3:
                x += delta * px / ln
                y += delta * py / ln
            x = max(0, min(x, cfg.output_shape[1] - 1))
            y = max(0, min(y, cfg.output_shape[0] - 1))
            cls_skeleton[test_image_id, w, :2] = (x * 4 + 2, y * 4 + 2)
            cls_skeleton[test_image_id, w, 2] = r0[w, int(round(y) + 1e-10), int(round(x) + 1e-10)]


        # map back to original images
        crops[test_image_id, :] = details[test_image_id - start_id, :]
        for w in range(cfg.nr_skeleton):
            cls_skeleton[test_image_id, w, 0] = cls_skeleton[test_image_id, w, 0] / cfg.data_shape[1] * (crops[test_image_id][2] - crops[test_image_id][0]) + crops[test_image_id][0]
            cls_skeleton[test_image_id, w, 1] = cls_skeleton[test_image_id, w, 1] / cfg.data_shape[0] * (crops[test_image_id][3] - crops[test_image_id][1]) + crops[test_image_id][1]
    return cls_skeleton


def prepare_results(test_data, cls_skeleton, cls_dets):
    cls_partsco = cls_skeleton[:, :, 2].copy().reshape(-1, cfg.nr_skeleton)

    cls_scores = 1
    dump_results = []
    cls_skeleton = np.concatenate(
        [cls_skeleton.reshape(-1, cfg.nr_skeleton * 3), (cls_scores * cls_partsco.mean(axis=1))[:, np.newaxis]],
        axis=1)
    for i in range(len(cls_skeleton)):
        result = dict(image_id=test_data['img_id'],
                      category_id=1,
                      score=float(round(cls_skeleton[i][-1], 4)),
                      keypoints=cls_skeleton[i][:-1].round(3).tolist())
        dump_results.append(result)
    return dump_results


def is_keyframe(img_id, interval=10):
    if img_id % interval == 0:
        return True
    else:
        return False


def pose_to_standard_mot(keypoints_list_list, dets_list_list):
    openSVAI_python_data_list = []

    num_keypoints_list = len(keypoints_list_list)
    num_dets_list = len(dets_list_list)
    assert(num_keypoints_list == num_dets_list)

    for i in range(num_dets_list):

        dets_list = dets_list_list[i]
        keypoints_list = keypoints_list_list[i]

        if dets_list == []:
            continue
        img_path = dets_list[0]["imgpath"]
        img_folder_path = os.path.dirname(img_path)
        img_name =  os.path.basename(img_path)
        img_info = {"folder": img_folder_path,
                    "name": img_name,
                    "id": [int(i)]}
        openSVAI_python_data = {"image":[], "candidates":[]}
        openSVAI_python_data["image"] = img_info

        num_dets = len(dets_list)
        num_keypoints = len(keypoints_list) #number of persons, not number of keypoints for each person
        candidate_list = []

        for j in range(num_dets):
            keypoints_dict = keypoints_list[j]
            dets_dict = dets_list[j]

            img_id = keypoints_dict["img_id"]
            det_id = keypoints_dict["det_id"]
            img_path = keypoints_dict["imgpath"]

            bbox_dets_data = dets_list[det_id]
            det = dets_dict["bbox"]
            if  det == [0, 0, 2, 2]:
                # do not provide keypoints
                candidate = {"det_bbox": [0, 0, 2, 2],
                             "det_score": 0}
            else:
                bbox_in_xywh = det[0:4]
                keypoints = keypoints_dict["keypoints"]

                track_score = sum(keypoints[2::3])/len(keypoints)/3.0

                candidate = {"det_bbox": bbox_in_xywh,
                             "det_score": 1,
                             "track_score": track_score,
                             "pose_keypoints_2d": keypoints}
            candidate_list.append(candidate)
        openSVAI_python_data["candidates"] = candidate_list
        openSVAI_python_data_list.append(openSVAI_python_data)
    return openSVAI_python_data_list


def x1y1x2y2_to_xywh(det):
    x1, y1, x2, y2 = det
    w, h = int(x2) - int(x1), int(y2) - int(y1)
    return [x1, y1, w, h]


def xywh_to_x1y1x2y2(det):
    x1, y1, w, h = det
    x2, y2 = x1 + w, y1 + h
    return [x1, y1, x2, y2]


def bbox_invalid(bbox):
    if bbox == [0, 0, 2, 2]:
        return True
    return False


if __name__ == '__main__':
    # global args
    parser = argparse.ArgumentParser()
    parser.add_argument('--video_path', '-v', type=str, dest='video_path', default="data/demo/video.mp4")
    parser.add_argument('--model', '-m', type=str, dest='test_model', default="weights/mobile-deconv/snapshot_296.ckpt")
    parser.add_argument('--detector','-d',type=str,dest='human_detector',default='yolo')
    args = parser.parse_args()
    print("Human Detector:{}".format(args.human_detector))
    args.bbox_thresh = 0.4
    detector=args.human_detector
    # initialize pose estimator
    initialize_parameters()
    pose_estimator = Tester(Network(), cfg)
    pose_estimator.load_weights(args.test_model)

    video_path = args.video_path
    visualize_folder = "data/demo/visualize"
    output_video_folder = "data/demo/videos"
    output_json_folder = "data/demo/jsons"
    video_name = os.path.basename(video_path)
    video_name = os.path.splitext(video_name)[0]
    image_folder = os.path.join("data/demo", video_name)
    visualize_folder = os.path.join(visualize_folder, video_name)
    output_json_path = os.path.join(output_json_folder, video_name+".json")
    output_video_path = os.path.join(output_video_folder, video_name+"_out_yolo.mp4")
    detect_folder=os.path.join("data/demo",'detect')
    if is_video(video_path):
        video_to_images(video_path)
        create_folder(visualize_folder)
        create_folder(output_video_folder)
        create_folder(output_json_folder)

        light_track(pose_estimator,detector,
                    image_folder, output_json_path,
                    visualize_folder, detect_folder,output_video_path)

        print("Finished video {}".format(output_video_path))

        ''' Display statistics '''
        print("total_time_ALL: {:.2f}s".format(total_time_ALL))
        print("total_time_DET: {:.2f}s".format(total_time_DET))
        print("total_time_POSE: {:.2f}s".format(total_time_POSE))
        print("total_num_FRAMES: {:d}".format(total_num_FRAMES))
        print("total_num_PERSONS: {:d}\n".format(total_num_PERSONS))
        print("Average FPS: {:.2f}fps".format(total_num_FRAMES / total_time_ALL))
        print("Average FPS excluding Pose Estimation: {:.2f}fps".format(total_num_FRAMES / (total_time_ALL - total_time_POSE)))
        print("Average FPS excluding Detection: {:.2f}fps".format(total_num_FRAMES / (total_time_ALL - total_time_DET)))
    else:
        print("Video does not exist.")
