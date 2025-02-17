import torchvision
import torchvision.datasets as dset
import torchvision.transforms as transforms
from torch.utils.data import DataLoader,Dataset
import matplotlib.pyplot as plt
import torchvision.utils
import numpy as np
import random
import os
from PIL import Image
import torch
from torch.autograd import Variable
import PIL.ImageOps    
import torch.nn as nn
from torch import optim
import torch.nn.functional as F
import cv2,pickle,sys
import argparse
import glob
from pathlib import Path
import cv2
from tqdm import tqdm
import pandas as pd
from yolov5_inference import yolo_detector
from utils.torch_utils import time_synchronized, select_device
from models.experimental import attempt_load
from collections import OrderedDict

from deepsort import *

'''
def get_gt(image,frame_id,gt_dict):

    if frame_id not in gt_dict.keys() or gt_dict[frame_id]==[]:
        return None,None,None

    frame_info = gt_dict[frame_id]

    detections = []
    ids = []
    out_scores = []
    for i in range(len(frame_info)):

        coords = frame_info[i]['coords']

        x1,y1,w,h = coords
        x2 = x1 + w
        y2 = y1 + h

        xmin = min(x1,x2)
        xmax = max(x1,x2)
        ymin = min(y1,y2)
        ymax = max(y1,y2)   

        detections.append([x1,y1,w,h])
        out_scores.append(frame_info[i]['conf'])

    return detections,out_scores


def get_dict(filename):
    with open(filename) as f:   
        d = f.readlines()

    d = list(map(lambda x:x.strip(),d))

    last_frame = int(d[-1].split(',')[0])

    gt_dict = {x:[] for x in range(last_frame+1)}

    for i in range(len(d)):
        a = list(d[i].split(','))
        a = list(map(float,a))  

        coords = a[2:6]
        confidence = a[6]
        gt_dict[a[0]].append({'coords':coords,'conf':confidence})

    return gt_dict

##may be we can use it to reduce Radius of interest 
def get_mask(filename):
    mask = cv2.imread(filename,0)
    mask = mask / 255.0
    return mask
'''

import motmetrics as mm


def get_object_with_ID(filename):
    lb = pd.read_table(filename, delim_whitespace=True,
                        names=('ID', 'x', 'y'),
                        dtype={'ID': np.uint16, 'x': np.uint16, 'y': np.uint16})
    
    cen = np.zeros((len(lb), 2), dtype=int)
    

    IDs = np.array(lb['ID'][:])
    cen[:, 0] = lb['x'][:]
    cen[:, 1] = lb['y'][:]

    return IDs, cen


def evaluate_tracking(tracking_GT_dir, tracking_detection_dir):
    detection_files = glob.glob(tracking_detection_dir + "/*.txt", recursive=True)
    list.sort(detection_files)
    
    # Create an accumulator that will be updated during each frame
    acc = mm.MOTAccumulator(auto_id=True)
    
    for i in tqdm(range(len(detection_files))):
        det_file = detection_files[i]
        gt_file = det_file.replace(tracking_detection_dir, tracking_GT_dir)
        
        detector_hypotheses, detector_centroids = get_object_with_ID(det_file)
        gt_objects, gt_centroids = get_object_with_ID(gt_file)
                
        distances = mm.distances.norm2squared_matrix(gt_centroids, detector_centroids, max_d2=25.) #
        
        # Call update once for per frame. For now, assume distances between
        # frame objects / hypotheses are given.
        acc.update(
            gt_objects,                     # Ground truth objects in this frame
            detector_hypotheses,            # Detector hypotheses in this frame
            distances                       # Distances from GT to hypotheses
        )

        
    mh = mm.metrics.create()
    summary = mh.compute(
        acc,
        metrics=mm.metrics.motchallenge_metrics,
        name='overall'
        )

    strsummary = mm.io.render_summary(
        summary,
        formatters=mh.formatters,
        namemap=mm.io.motchallenge_metric_names
    )
    print(strsummary)

    return strsummary

# Get the bounding boxes for an image file
def get_bb(image_labels, image_file):
    label = pd.read_table(image_labels[image_file], delim_whitespace=True,
                        names=('~', 'x', 'y', 'w', 'h'),
                        dtype={'~': np.uint8, 'x': np.float32, 'y': np.float32, 'w': np.float32,
                               'h': np.float32})
    label = label.drop('~', axis=1)
    #label['conf'] = 0.99
    return label

# Convert centroid and height-width bb format to initial and endpoint format
def cvt_bb(label):
    rect = []
    for car_no in range(len(label)):
        startX = label['x'][car_no] - label['w'][car_no]/2
        startY = label['y'][car_no] - label['h'][car_no]/2
        #endX = label['x'][car_no] + label['w'][car_no]/2
        #endY = label['y'][car_no] + label['h'][car_no]/2
        
        endX = label['w'][car_no]
        endY = label['h'][car_no]
        
        conf = 0.99
        rect.append((startX, startY, endX, endY,conf))
    return rect



if __name__ == '__main__':
    # construct the argument parse and parse the arguments
    ap = argparse.ArgumentParser()
    ap.add_argument("-w", "--weights", type=str, default='best.pt', help='model.pt path(s)')
    ap.add_argument("-i", "--img", type=int, default= 1024, help='image size to prediction')
    ap.add_argument("-d", "--inp", type=str, default='input/images4', help='Input directory')
    ap.add_argument("-m", "--maxage", type=int, default=10, help='maximum # of frame to approximate')
    ap.add_argument("-l", "--lab", type=str, default='input/labels4', help='Labels directory')
    ap.add_argument("-s", "--savevid", type=str, default='True', help='Whether video will be saved or not')
    args = vars(ap.parse_args())    
    #Load detections for the video. Options available: yolo,ssd and mask-rcnn
    # filename = 'det/det_ssd512.txt'
    # gt_dict = get_dict(filename)

    #Initialize deep sort.
    deepsort = deepsort_rbc()

    #frame_id = 1

    images_dir = args["inp"]+'/'   # 'input/images3/'
    
    
    img_name = glob.glob(images_dir + "*.jpg")
    img_name.sort()
    img_list = []
    for name in img_name:
        name = (Path(name).stem)+'.jpg'
        img_list.append(name)

    #list.sort(img_list)
    print("Total images: " + str(len(img_list)))
    
    
    image_file = os.path.join(args["inp"],img_list[0])
    frame0 = cv2.imread(image_file)
    (H, W) = frame0.shape[:2]

    images_dir = args["inp"]
    labels_dir = args["lab"]

    image_labels = {}


    for img in glob.glob(images_dir + "/*.jpg", recursive=True):
        label = img.replace(images_dir, labels_dir)
        label = label.replace(".jpg", ".txt")
        image_labels[img] = label

    images = list(image_labels.keys())
    list.sort(images)

    print("Total images: " + str(len(image_labels)))


    
    trajectory = OrderedDict()
    for i in tqdm(range(len(image_labels))):
        # read the next frame from the video stream and resize it
        image_file = images[i]
        label = get_bb(image_labels, image_file)
        rects = cvt_bb(label) * np.array([W, H, W, H, 1.0])
        
        rects = np.array(rects)

        detections = rects[:,0:4]    ##we need to pass [x1,y1,w,h]
        out_scores = rects[:,4]
        #detections = np.array(detections)
        #out_scores = np.array(out_scores) 
        frame = cv2.imread(image_file)
        tracker,detections_class = deepsort.run_deep_sort(frame,out_scores,detections)

        valid_tracks = 0
        for track in tracker.tracks:
            if not track.is_confirmed() or track.time_since_update > 1:
                continue
            else:
                valid_tracks +=1

        output_table = np.zeros((valid_tracks, 3), dtype=int)
        idx = 0
        
        for track in tracker.tracks:
            if not track.is_confirmed() or track.time_since_update > 1:
                continue
            
            bbox = track.to_tlbr() #Get the corrected/predicted bounding box (Output bbox from tracker)
            id_num = str(track.track_id) #Get the ID for the particular track.
            #features = track.features #Get the feature vector corresponding to the detection.
            centroid = [int((bbox[0] + bbox[2]) / 2), int((bbox[1] + bbox[3]) / 2)]
            try:
                trajectory[id_num].append(centroid)
            except:
                trajectory[id_num] = list([centroid])
            object_trajectory = trajectory[id_num]

            output_table[idx, 0] = id_num
            output_table[idx, 1:3] = np.array(centroid, dtype=int)
            idx += 1
            
            
        #cv2.imshow("Frame", frame)
        #cv2.waitKey(0) & 0xFF
        tracking_gt_dir = "input/fisheye_day_tracking/"
        if not os.path.exists(tracking_gt_dir):
            os.mkdir(tracking_gt_dir)
        
        #print(image_labels)
        image_file = image_file.replace('.jpg','.txt')
        filename = image_file.replace(args["inp"], tracking_gt_dir)
        np.savetxt(filename, output_table, fmt='%d')
        
    del trajectory
    print("Done with GT")





    weights = args["weights"]
    device = select_device('')
    half = device.type != 'cpu'  # half precision only supported on CUDA
    model = attempt_load(weights, map_location=device)  # load FP32 model   ##here model checks whether one model or ensemble of models will be loaded

    yolo = yolo_detector(model, device, half)

    '''
    if args["savevid"]:
        ##to save into a video
        image_file = os.path.join(args["inp"],img_list[0])
        frame0 = cv2.imread(image_file)

        fourcc = cv2.VideoWriter_fourcc(*'mp4v') 
        height,width,layers= frame0.shape
        video=cv2.VideoWriter('deepsort_video.avi', fourcc, 20, (width,height))
    '''
    # fourcc = cv2.VideoWriter_fourcc(*'XVID')
    # out = cv2.VideoWriter('ssd_out_3.avi',fourcc, 20.0, (1920,1080))
    trajectory = OrderedDict()

    #Iterate over each frame from the input image folder 
    for i in tqdm(range(0, len(image_labels))):
        #print(frame_id)     
        #ret,frame = cap.read()
        #dim = (1920,1080)
        #frame = cv2.resize(frame, dim, interpolation = cv2.INTER_AREA)   
        #frame = frame * mask
        #frame = frame.astype(np.uint8)
        #detections,out_scores = get_gt(frame,frame_id,gt_dict)
        

        parent_dir = args["inp"]
        image_file = os.path.join(parent_dir,img_list[i])  ##path of an image
        image_size = args["img"]

        ######################################
        ###prediction part of YOLOV5
        ### obtain our output predictions, and initialize the list of bounding box rectangles
        ######################################

        rects , frame = yolo.infer_on_single_img(image_file, image_size)  ##output from yolo detector. Output is a list of list.
        ##rects format is [ [x,y,w,h,conf],[48,125,487,154,0.54], [47,89,147,69,0.94],....... #n of elements in this list of a frame]
        ## frame is the raw frame

        t1 = time_synchronized()
        rects = np.array(rects)

        detections = rects[:,0:4]    ##we need to pass [x1,y1,w,h]
        out_scores = rects[:,4]

        '''
        if detections is None:
            print("No dets")
            frame_id+=1
            continue
        '''
        #detections = np.array(detections)
        #out_scores = np.array(out_scores) 

        tracker,detections_class = deepsort.run_deep_sort(frame,out_scores,detections)

        valid_tracks = 0
        for track in tracker.tracks:
            if not track.is_confirmed() or track.time_since_update > 1:
                continue
            else:
                valid_tracks +=1

        output_table = np.zeros((valid_tracks, 3), dtype=int)
        idx = 0
        
        for track in tracker.tracks:
            if not track.is_confirmed() or track.time_since_update > 1:
                continue
            
            bbox = track.to_tlbr() #Get the corrected/predicted bounding box (Output bbox from tracker)
            id_num = str(track.track_id) #Get the ID for the particular track.
            #features = track.features #Get the feature vector corresponding to the detection.
            centroid = [int((bbox[0] + bbox[2]) / 2), int((bbox[1] + bbox[3]) / 2)]
            try:
                trajectory[id_num].append(centroid)
            except:
                trajectory[id_num] = list([centroid])
            object_trajectory = trajectory[id_num]

            output_table[idx, 0] = id_num
            output_table[idx, 1:3] = np.array(centroid, dtype=int)
            idx += 1

            #Draw bbox from tracker.
            cv2.rectangle(frame, (int(bbox[0]), int(bbox[1])), (int(bbox[2]), int(bbox[3])),(255,255,255), 2)
            cv2.putText(frame, str(id_num),(int(bbox[0]), int(bbox[1])),0, 5e-3 * 200, (0,255,0),2)
            pts = len(object_trajectory)
            if pts > 1:
                cv2.polylines(frame, [np.array(object_trajectory)], False, (0, 255, 255), 5)

            #Draw bbox from detector(Direct output of detector). Just to compare.
            for det in detections_class:
                bbox = det.to_tlbr()
                cv2.rectangle(frame,(int(bbox[0]), int(bbox[1])), (int(bbox[2]), int(bbox[3])),(255,255,0), 2)

        
        t2 = time_synchronized()
        print('Tracking takes %.3fs' % (t2 - t1))
        #cv2.imshow('frame',frame)
        #video.write(frame)
        tracking_detection_dir = "input/fisheye_day_detection/"
        if not os.path.exists(tracking_detection_dir):
            os.mkdir(tracking_detection_dir)
        
        #print(image_labels)
        image_file = image_file.replace('.jpg','.txt')
        filename = image_file.replace(args["inp"], tracking_detection_dir)
        #print(filename)
        np.savetxt(filename, output_table, fmt='%d')
        
        # if cv2.waitKey(1) & 0xFF == ord('q'):
        #     break

        #frame_id+=1

    

    tracking_detection_dir = "input/fisheye_day_detection"
    tracking_GT_dir = "input/fisheye_day_tracking"
    summary = evaluate_tracking(tracking_GT_dir, tracking_detection_dir)

    ##saving result into a txt file
    text_file = open("mot_result_cen.txt", "w")
    n = text_file.write(summary)
    text_file.close()