import os
import sys
import torch
import random
import argparse
from torch import nn
import matplotlib.pyplot as plt
import torch.backends.cudnn as cudnn

import matplotlib.pyplot as plt

# Import dataloaders
import Data.cifar10 as cifar10
import Data.cifar100 as cifar100
import Data.tiny_imagenet as tiny_imagenet

# Import network architectures
from Net.resnet_tiny_imagenet import resnet50 as resnet50_ti
from Net.resnet import resnet50, resnet110
from Net.wide_resnet import wide_resnet_cifar
from Net.densenet import densenet121

# Import metrics to compute
from Metrics.metrics import test_classification_net_logits
from Metrics.metrics import ECELoss, AdaptiveECELoss, ClasswiseECELoss, ClassECELoss, posnegECELoss, binsECELoss, diffECELoss
from Metrics.plots import reliability_plot, pos_neg_ece_plot, ece_acc_plot, ece_iters_plot, temp_acc_plot, diff_ece_plot, bins_over_conf_plot

# Import temperature scaling and NLL utilities
from temperature_scaling import ModelWithTemperature

# Import unpickling logits and labels
from evaluate_scripts.unpickle_probs import unpickle_probs

# Dataset params
dataset_num_classes = {
    'cifar10': 10,
    'cifar100': 100,
    'imagenet': 1000,
    'svhn': 10
}

dataset_loader = {
    'cifar10': cifar10,
    'cifar100': cifar100,
    'imagenet': imagenet,
    'svhn': svhn
}

# Mapping model name to model function
models = {
    'resnet50': resnet110,
    'resnet110_sd': resnet110_sd,
    'wide_resnet_32': wide_resnet_32,
    'densenet40': densenet40,
    'lenet5': lenet5,
    'densenet161': densenet161,
    'resnet152': resnet152,
    'resnet152_sd': resnet152_sd
}


def parseArgs():
    default_dataset = 'cifar10'
    dataset_root = './'
    model = 'resnet110'
    save_loc = './'
    save_plots_loc = './'
    saved_model_name = 'resnet110_cross_entropy_350.model'
    num_bins = 25
    model_name = None
    train_batch_size = 128
    test_batch_size = 128
    cross_validation_error = 'ece'
    trained_loss = 'cross_entropy'
    logits_path = '/mnt/dsi_vol1/users/frenkel2/data/calibration/trained_models/spline/logits/'
    logits_file = 'probs_resnet110_c10_logits.p'

    parser = argparse.ArgumentParser(
        description="Evaluating a single model on calibration metrics.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--dataset", type=str, default=default_dataset,
                        dest="dataset", help='dataset to test on')
    parser.add_argument("--dataset-root", type=str, default=dataset_root,
                        dest="dataset_root", help='root path of the dataset (for tiny imagenet)')
    parser.add_argument("--model-name", type=str, default=model_name,
                        dest="model_name", help='name of the model')
    parser.add_argument("--model", type=str, default=model, dest="model",
                        help='Model to test')
    parser.add_argument("--save-path", type=str, default=save_loc,
                        dest="save_loc",
                        help='Path to import the model')
    parser.add_argument("--saved_model_name", type=str, default=saved_model_name,
                        dest="saved_model_name", help="file name of the pre-trained model")
    parser.add_argument("--num-bins", type=int, default=num_bins, dest="num_bins",
                        help='Number of bins')
    parser.add_argument("-g", action="store_true", dest="gpu",
                        help="Use GPU")
    parser.set_defaults(gpu=True)
    parser.add_argument("-da", action="store_true", dest="data_aug",
                        help="Using data augmentation")
    parser.set_defaults(data_aug=True)
    parser.add_argument("-b", type=int, default=train_batch_size,
                        dest="train_batch_size", help="Batch size")
    parser.add_argument("-tb", type=int, default=test_batch_size,
                        dest="test_batch_size", help="Test Batch size")
    parser.add_argument("--cverror", type=str, default=cross_validation_error,
                        dest="cross_validation_error", help='Error function to do temp scaling')
    parser.add_argument("-log", action="store_true", dest="log",
                        help="whether to print log data")
    parser.add_argument("-plot", action="store_true", dest="create_plots",
                        help="whether to create plots of ECE vs. temperature scaling iterations")
    parser.add_argument("-posneg", action="store_true", dest="pos_neg_ece",
                        help="whether to calculate positiv and negative ECE for each class")
    parser.add_argument("-unc", action="store_true", dest="uncalibrated_check",
                        help="whether to calculate ECE for each class of uncalibrated model")
    parser.add_argument("-acc", action="store_true", dest="acc_check",
                        help="whether to calculate ECE for each class only if accuracy gets better")
    parser.add_argument("-iters", type=int, default=1,
                        dest="temp_opt_iters", help="number of temprature scaling iterations")
    parser.add_argument("-init_temp", type=float, default=2.5,
                        dest="init_temp", help="initial temperature for temperature scaling")
    parser.add_argument("-const_temp", action="store_true", dest="const_temp",
                        help="whether to use constant temperature on all classes")
    parser.add_argument("--save-path-plots", type=str, default=save_plots_loc,
                        dest="save_plots_loc",
                        help='Path to save plots')
    parser.add_argument("--loss", type=str, default=trained_loss,
                        dest="trained_loss",
                        help='Trained loss(cross_entropy/focal_loss/focal_loss_adaptive/mmce/mmce_weighted/brier_score)')
    parser.add_argument("--logits_path", type=str, default=logits_path,
                        dest="logits_path",
                        help='Path of saved logits')
    parser.add_argument("--logits_file", type=str, default=logits_file,
                        dest="logits_file",
                        help='File of saved logits')

    return parser.parse_args()


if __name__ == "__main__":

    # Checking if GPU is available
    cuda = False
    if (torch.cuda.is_available()):
        cuda = True

    # Setting additional parameters
    torch.manual_seed(1)
    device = torch.device("cuda" if cuda else "cpu")

    args = parseArgs()

    if args.model_name is None:
        args.model_name = args.model

    dataset = args.dataset
    dataset_root = args.dataset_root
    model_name = args.model_name
    save_loc = args.save_loc
    saved_model_name = args.saved_model_name
    num_bins = args.num_bins
    cross_validation_error = args.cross_validation_error
    temp_opt_iters = args.temp_opt_iters
    const_temp = args.const_temp
    create_plots = args.create_plots
    save_plots_loc = args.save_plots_loc
    init_temp = args.init_temp
    pos_neg_ece = args.pos_neg_ece
    uncalibrate_check = args.uncalibrated_check
    font_size = 10
    trained_loss = args.trained_loss
    acc_check = args.acc_check
    logits_file =  args.logits_file
    logits_path = args.logits_path

    # Taking input for the dataset
    num_classes = dataset_num_classes[dataset]

    model = models[model_name]

    nll_criterion = nn.CrossEntropyLoss().cuda()
    ece_criterion = ECELoss().cuda()
    adaece_criterion = AdaptiveECELoss().cuda()
    cece_criterion = ClasswiseECELoss().cuda()
    csece_criterion = ClassECELoss().cuda()
    posneg_csece_criterion = posnegECELoss().cuda()
    bins_csece_criterion = binsECELoss().cuda()
    diff_ece_criterion = diffECELoss().cuda()
    
    file = logits_path + logits_file
    (logits_val, labels_val), (logits_test, labels_test) = unpickle_probs(file)

    p_ece = ece_criterion(logits_test, labels_test).item()
    
    # Printing the required evaluation metrics
    if args.log:
        print ('ECE: ' + str(p_ece))

    scaled_model = ModelWithTemperature(net, args.log, const_temp=const_temp)
    scaled_model.set_temperature(val_loader, temp_opt_iters, cross_validate=cross_validation_error, init_temp=init_temp, acc_check=acc_check)
    logits, labels = get_logits_labels(test_loader, scaled_model)
    ece = ece_criterion(logits, labels).item()
    
    # For const temp scaling
    logits_const, labels_const = get_logits_labels_const(test_loader, scaled_model, const_temp=True)
    ece_const = ece_criterion(logits_const, labels_const).item()
    
    if const_temp:
        T_opt = scaled_model.get_temperature()
    else:
        T_opt, T_csece_opt = scaled_model.get_temperature()
        if create_plots:
            ece_iters_plot(temp_opt_iters, scaled_model, save_plots_loc, dataset, args.model, trained_loss, init_temp, acc_check)
            
    conf_matrix, accuracy, _, predictions, confidences = test_classification_net_logits(logits, labels)
    reliability_plot(confidences, predictions, labels, save_plots_loc, dataset, args.model, trained_loss, num_bins=num_bins, scaling_related='after', save=True)
    
    _, _, _, predictions_const, confidences_const = test_classification_net_logits(logits_const, labels_const)
    reliability_plot(confidences_const, predictions_const, labels_const, save_plots_loc, dataset, args.model,
                     trained_loss, num_bins=num_bins, scaling_related='after_const', save=True)

    adaece = adaece_criterion(logits, labels).item()
    cece = cece_criterion(logits, labels).item()
    csece, accuracies = csece_criterion(logits, labels)
    csece_const, accuracies_const = csece_criterion(logits_const, labels_const)
    if uncalibrate_check:
        csece_uncalibated, accuracies_uncalibated = csece_criterion(logits*init_temp, labels)
    if pos_neg_ece:
        csece_high, csece_low, _ = bins_csece_criterion(logits, labels)
        csece_pos, csece_neg, accuracies = posneg_csece_criterion(logits, labels)
    nll = nll_criterion(logits, labels).item()

    res_str += '&{:.4f}({:.2f})&{:.4f}&{:.4f}&{:.4f}'.format(nll,  T_opt,  ece,  adaece, cece)

    if create_plots:
        if pos_neg_ece:
            # pos and neg ECE vs. accuracy per class
            pos_neg_ece_plot(accuracies, csece_pos, csece_neg, save_plots_loc, dataset, args.model, trained_loss, acc_check=acc_check, scaling_related='after',
                             const_temp=const_temp)
            # high and low bins ECE vs. accuracy per class
            pos_neg_ece_plot(accuracies, csece_high, csece_low, save_plots_loc, dataset, args.model, trained_loss, acc_check=acc_check, scaling_related='after_bins',
                             const_temp=const_temp)
        # ECE vs. accuracy per class
        ece_acc_plot(accuracies, csece, save_plots_loc, dataset, args.model, trained_loss, acc_check=acc_check, scaling_related='after', const_temp=const_temp)
        if not const_temp:
            # Temperature vs. accuracy per class
            temp_acc_plot(accuracies, T_csece_opt, T_opt, save_plots_loc, dataset, args.model, trained_loss, acc_check=acc_check, const_temp=const_temp)
        """
        # ECE vs. accuracy per class - Difference between before and after temperature scaling
        # Class-based temperature scaling diff
        diff_ece_plot(accuracies, csece, p_csece, save_plots_loc, dataset, args.model, trained_loss, acc_check=acc_check, scaling_type='class_based')
        # Single temperature scaling diff
        diff_ece_plot(accuracies, csece_const, p_csece, save_plots_loc, dataset, args.model, trained_loss, acc_check=acc_check, scaling_type='single')
        # ECE vs. accuracy per class - Difference between class-based and single temperature scaling
        diff_ece_plot(accuracies, csece, csece_const, save_plots_loc, dataset, args.model, trained_loss, acc_check=acc_check, scaling_type='class_based_single')
        """
        if uncalibrate_check:
            ece_acc_plot(accuracies_uncalibated, csece_uncalibated, save_plots_loc, dataset, args.model, trained_loss, acc_check=acc_check, scaling_related='after', unc=True)
                
    if args.log:
        print ('Optimal temperature: ' + str(T_opt))
        if not const_temp:
            print ('Optimal classes tempeatures: ' + str(T_csece_opt))
        print (conf_matrix)
        print ('Test error: ' + str((1 - accuracy)))
        print ('Test NLL: ' + str(nll))
        print ('ECE (Class-based temp scaling): ' + str(ece))
        print ('ECE (constant temp scaling): ' + str(ece_const))
        print ('AdaECE: ' + str(adaece))
        print ('Classwise ECE: ' + str(cece))
        print ('Classes ECE: ' + str(csece))
        print ('Classes accuracies: ' + str(accuracies))

    # Test NLL & ECE & AdaECE & Classwise ECE
    print(res_str)
