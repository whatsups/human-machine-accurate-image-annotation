# -*- coding: utf-8 -*-
from __future__ import print_function
import os
from glob import glob
import json
from PIL import Image, ImageDraw
import numpy as np
import torch
import warnings
from utils import iou, poly01_to_poly0g
from models.model import PolygonModel
import argparse

warnings.filterwarnings("ignore")
device = 'cuda' if torch.cuda.is_available() else 'cpu'
selected_classes = ['person', 'car', 'truck', 'bicycle', 'motorcycle',
                    'rider', 'bus', 'train']

# 迭代更改, 直到IoU达到某个值, 这个跟论文中的做法是不同的

def get_score(net, dataset='test', maxnum=float('inf'), saved=False, iou_threshold=0.80):

    save_result_dir = '/data/duye/cityscape/save_img/paper_save2/'
    if not os.path.exists(save_result_dir):
        os.makedirs(save_result_dir)
    selected_classes = ['person', 'car', 'truck', 'bicycle', 'motorcycle',
                        'rider', 'bus', 'train']
    iou_score = {}
    nu = {}  # Intersection
    de = {}  # Union
    iouss = {}
    for cls in selected_classes:
        iou_score[cls] = 0.0
        nu[cls] = 0.0
        de[cls] = 0.0
        iouss[cls] = []

    count = 0
    print('origin count:', count)
    files_test = glob('img/{}/*/*.png'.format(dataset))  # 原始图像集,dataset指定训练集/测试集/验证集
    files_val = glob('img/val/*/*.png')
    print('test:', len(files_test))
    print('val :', len(files_val))
    files = files_test # 只有test
    print('All: ', len(files))
    less2 = 0
    NUM_clicks = []
    for idx, file in enumerate(files):
        json_file = 'label' + file[3:-15] + 'gtFine_polygons.json'
        json_object = json.load(open(json_file))
        H = json_object['imgHeight']
        W = json_object['imgWidth']
        objects = json_object['objects']
        img = Image.open(file).convert('RGB')  # PIL
        I = np.array(img)
        img_gt = Image.open(file).convert('RGB')

        for obj in objects:
            if obj['label'] in selected_classes:
                polygon = np.array(obj['polygon'])  # 在原图片中的坐标
                # find min/max X,Y
                minW, minH = np.min(polygon, axis=0)
                maxW, maxH = np.max(polygon, axis=0)
                curW = maxW - minW
                curH = maxH - minH
                # Extend 10% ~ 测试鲁邦性，可以把这个扩展5%-15%
                extendW = int(round(curW * 0.1))
                extendH = int(round(curH * 0.1))
                leftW = np.maximum(minW - extendW, 0)  # minrow, mincol, maxrow, maxcol
                leftH = np.maximum(minH - extendH, 0)
                rightW = np.minimum(maxW + extendW, W)
                rightH = np.minimum(maxH + extendH, H)
                objectW = rightW - leftW
                objectH = rightH - leftH
                scaleH = 224.0 / objectH
                scaleW = 224.0 / objectW

                # get gt in ~ [224,224]
                gt_224 = []
                for vertex in polygon:
                    x = (vertex[0] - leftW) * (224.0 / objectW)
                    y = (vertex[1] - leftH) * (224.0 / objectH)
                    # 防溢出
                    x = np.maximum(0, np.minimum(223, x))
                    y = np.maximum(0, np.minimum(223, y))
                    gt_224.append([x, y])
                # To (28, 28)
                gt_224 = np.array(gt_224)
                gt_28 = gt_224 / (224 * 1.0)
                # To (0, g), int值,即在28*28中的坐标值 道格拉斯算法多边形曲线拟合，这里有一个去除重点的过程
                gt_28 = poly01_to_poly0g(gt_28, 28)
                # To indexes
                seq_len = 71
                gt_index = np.zeros([seq_len])
                point_num = len(gt_28)
                cnts = 0
                if point_num < seq_len:  # < 70
                    for point in gt_28:
                        x = point[0]
                        y = point[1]
                        indexs = y * 28 + x
                        gt_index[cnts] = indexs
                        cnts += 1
                    # end point
                    gt_index[cnts] = 28 * 28
                    cnts += 1
                    for ij in range(cnts, seq_len):
                        gt_index[ij] = 28 * 28
                        cnts += 1
                else:
                    # 点数过多的话只取前70个点是不对的, 这里应该考虑一下如何选取点
                    for iii in range(seq_len - 1):
                        point = polygon[iii]  # 取点
                        x = point[0]
                        y = point[1]
                        indexs = y * 28 + x
                        gt_index[cnts] = indexs
                        cnts += 1
                    # EOS
                    gt_index[seq_len - 1] = 28 * 28

                gt_index = torch.tensor(gt_index).unsqueeze(0).to(device)

                img_new = img.crop(box=(leftW, leftH, rightW, rightH)).resize((224, 224), Image.BILINEAR)
                I_obj = I[leftH:rightH, leftW:rightW, :]
                # To PIL image
                I_obj_img = Image.fromarray(I_obj)
                # resize
                I_obj_img = I_obj_img.resize((224, 224), Image.BILINEAR)
                I_obj_new = np.array(I_obj_img)  # (H, W, C)
                I_obj_new = I_obj_new.transpose(2, 0, 1)  # (C, H, W)
                # 归一化
                I_obj_new = I_obj_new / 255.0
                I_obj_tensor = torch.from_numpy(I_obj_new)  # (C, H, W)
                I_obj_tensor = torch.tensor(I_obj_tensor.unsqueeze(0), dtype=torch.float).cuda()
                color = [np.random.randint(0, 255) for _ in range(3)]
                color += [150]
                color = tuple(color)

                with torch.no_grad():
                    pre_v2 = None
                    pre_v1 = None
                    result_dict = net(I_obj_tensor, pre_v2, pre_v1,
                                      mode='test', temperature=0.0)  # (bs, seq_len)

                result = result_dict['pred_polys']
                # [0, 224] index 0: only one sample in mini-batch here
                pred_x = result_dict['final_pred_x'].cpu().numpy()[0]
                pred_y = result_dict['final_pred_y'].cpu().numpy()[0]
                pred_lengths = result_dict['lengths'].cpu().numpy()[0]
                pred_len = np.sum(pred_lengths) - 1  # sub EOS
                vertices1 = []
                vertices2 = []
                # Get the pred poly
                for i in range(pred_len):
                    vert = (pred_x[i] / scaleW + leftW,
                            pred_y[i] / scaleH + leftH)
                    vertices1.append(vert)

                if len(vertices1) < 2:
                    nu[obj['label']] = 0
                    de[obj['label']] = 0
                    less2 += 1
                    iouss[obj['label']].append(0)
                    continue

                #  GT
                for points in polygon:
                    vertex = (points[0], points[1])
                    vertices2.append(vertex)

                # calculate IoU
                iou_cur, nu_cur, de_cur = iou(vertices1, vertices2, H, W)

                # 得到IoU
                if iou_cur >= iou_threshold:
                    nu[obj['label']] += nu_cur
                    de[obj['label']] += de_cur
                    NUM_clicks.append(0)
                    iouss[obj['label']].append(iou_cur)
                else:
                    # 否则, 就更正
                    with torch.no_grad():
                        pre_v2 = None
                        pre_v1 = None
                        result_dict = net(I_obj_tensor, pre_v2, pre_v1,
                                          mode='interaction_loop', temperature=0.0,
                                          gt_28=gt_index)  # (bs, seq_len)

                    result = result_dict['pred_polys']
                    # [0, 224] index 0: only one sample in mini-batch here
                    pred_x = result_dict['final_pred_x'].cpu().numpy()[0]
                    pred_y = result_dict['final_pred_y'].cpu().numpy()[0]
                    pred_lengths = result_dict['lengths'].cpu().numpy()[0]
                    num_clicks = result_dict['click_num']
                    num_clicks = np.mean(np.array(num_clicks))
                    # clicks
                    NUM_clicks.append(num_clicks)
                    pred_len = np.sum(pred_lengths) - 1  # sub EOS
                    vertices1 = []
                    vertices2 = []
                    # Get the pred poly
                    for i in range(pred_len):
                        vert = (pred_x[i] / scaleW + leftW,
                                pred_y[i] / scaleH + leftH)
                        vertices1.append(vert)

                    # pred-draw
                    if saved:
                        try:
                            drw = ImageDraw.Draw(img, 'RGBA')
                            drw.polygon(vertices1, color, outline='darkorange')
                        except TypeError:
                            continue
                    if len(vertices1) < 2:
                        nu[obj['label']] = 0
                        de[obj['label']] = 0
                        less2 += 1
                        iouss[obj['label']].append(0)
                        continue
                    #  GT
                    for points in polygon:
                        vertex = (points[0], points[1])
                        vertices2.append(vertex)
                    if saved:
                        #  GT draw
                        drw_gt = ImageDraw.Draw(img_gt, 'RGBA')
                        drw_gt.polygon(vertices2, color, outline='white')
                    # calculate IoU
                    iou_tmp, nu_cur, de_cur = iou(vertices1, vertices2, H, W)
                    nu[obj['label']] += nu_cur
                    de[obj['label']] += de_cur
                    iouss[obj['label']].append(iou_tmp)

        count += 1
        if saved:
            print('saving test result image...')
            img.save(save_result_dir + str(idx) + '_pred_pp.png', 'PNG')
            img_gt.save(save_result_dir + str(idx) + '_gt_pp.png', 'PNG')

        if count >= maxnum:
            break
        print('count {} over'.format(count))

    # IoU
    for cls in iou_score:
        iou_score[cls] = nu[cls] * 1.0 / de[cls] if de[cls] != 0 else 0

    mean_over_class = {}
    means = 0.
    for cls in iouss:
        mean_over_class[cls] = np.mean(np.array(iouss[cls]))
        means += mean_over_class[cls]
    means = means / 8.

    # return
    return iou_score, less2, nu, de, np.mean(np.array(NUM_clicks)), mean_over_class, means


if __name__ == '__main__':
    print('Simulation with iou threshold.')
    parser = argparse.ArgumentParser(description='manual to this script')
    parser.add_argument('-i', '--iou', type=float, default=1)
    parser.add_argument('-l', '--loop_T', type=int, default=1)
    args = parser.parse_args()
    iou_threshold = args.iou
    loop_T = args.loop_T

    load_model = 'ResNext_Plus_RL2_retain_Epoch1-Step4000_ValIoU0.6316584628283326.pth'
    polynet_pretrained = '/data/duye/pretrained_models/FPNRLtrain/' + load_model

    net = PolygonModel(predict_delta=True, loop_T=loop_T).to(device)
    net.load_state_dict(torch.load(polynet_pretrained))
    net.eval()
    print('Pretrained model \'{}\' loaded!'.format(load_model))

    ious_test, less2_test, nu_test, de_test, clicks, mean_over_class, gt_iou_mean = get_score(net, saved=True,
                                                                iou_threshold=iou_threshold)

    print('Correct In real simulation with threshold IoU greater than {}, with loopT threshold {}'.format(
        iou_threshold,
        loop_T))

    print('pre-IoU: ', ious_test)
    print('Num Clicks:', clicks)

    print('True, mean_over_class:', mean_over_class)
    print('True, mean iou:', gt_iou_mean)