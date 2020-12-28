#!/usr/bin/env python3

# Copyright 2020 The Johns Hopkins University (author: Jiatong Shi & Shuai Guo)



import torch
import os
import time
import numpy as np

from SVS.model.utils.gpu_util import use_single_gpu
from SVS.model.utils.SVSDataset import SVSDataset, SVSCollator
from SVS.model.network import GLU_TransformerSVS, TransformerSVS, LSTMSVS, ConformerSVS
from SVS.model.utils.loss import MaskedLoss
from SVS.model.utils.utils import AverageMeter, record_info, log_figure, spectrogram2wav
from SVS.model.utils.utils import train_one_epoch, save_checkpoint, validate, record_info, collect_stats, save_model
from SVS.model.layers.global_mvn import GlobalMVN
import SVS.tools.metrics as Metrics

def count_parameters(model):
    return sum(p.numel() for p in model.parameters() if p.requires_grad)

def infer(args):
    torch.cuda.set_device(args.gpu_id)
    print(f"GPU {args.gpu_id} is used")
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.enabled = False
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # prepare model
    if args.model_type == "GLU_Transformer":
        model = GLU_TransformerSVS(phone_size=args.phone_size,
                                embed_size=args.embedding_size,
                                hidden_size=args.hidden_size,
                                glu_num_layers=args.glu_num_layers,
                                dropout=args.dropout,
                                output_dim=args.feat_dim,
                                dec_nhead=args.dec_nhead,
                                dec_num_block=args.dec_num_block,
                                n_mels=args.n_mels,
                                double_mel_loss=args.double_mel_loss,
                                local_gaussian=args.local_gaussian,
                                device=device)
    elif args.model_type == "LSTM":
        model = LSTMSVS(phone_size=args.phone_size,
                        embed_size=args.embedding_size,
                        d_model=args.hidden_size,
                        num_layers=args.num_rnn_layers,
                        dropout=args.dropout,
                        d_output=args.feat_dim,
                        n_mels=args.n_mels,
                        double_mel_loss=args.double_mel_loss,
                        device=device,
                        use_asr_post=args.use_asr_post)
    elif args.model_type == "GRU_gs":
        model = GRUSVS_gs(phone_size=args.phone_size,
                        embed_size=args.embedding_size,
                        d_model=args.hidden_size,
                        num_layers=args.num_rnn_layers,
                        dropout=args.dropout,
                        d_output=args.feat_dim,
                        n_mels=args.n_mels,
                        double_mel_loss=args.double_mel_loss,
                        device=device,
                        use_asr_post=args.use_asr_post)
    elif args.model_type == "PureTransformer":
        model = TransformerSVS(phone_size=args.phone_size,
                                        embed_size=args.embedding_size,
                                        hidden_size=args.hidden_size,
                                        glu_num_layers=args.glu_num_layers,
                                        dropout=args.dropout,
                                        output_dim=args.feat_dim,
                                        dec_nhead=args.dec_nhead,
                                        dec_num_block=args.dec_num_block,
                                        n_mels=args.n_mels,
                                        double_mel_loss=args.double_mel_loss,
                                        local_gaussian=args.local_gaussian,
                                        device=device)
    elif args.model_type == "Conformer":
        model = ConformerSVS(phone_size=args.phone_size,
                            embed_size=args.embedding_size,
                            
                            enc_attention_dim=args.enc_attention_dim, 
                            enc_attention_heads=args.enc_attention_heads, 
                            enc_linear_units=args.enc_linear_units, 
                            enc_num_blocks=args.enc_num_blocks,
                            enc_dropout_rate=args.enc_dropout_rate, 
                            enc_positional_dropout_rate=args.enc_positional_dropout_rate, 
                            enc_attention_dropout_rate=args.enc_attention_dropout_rate,
                            enc_input_layer=args.enc_input_layer, 
                            enc_normalize_before=args.enc_normalize_before, 
                            enc_concat_after=args.enc_concat_after,
                            enc_positionwise_layer_type=args.enc_positionwise_layer_type, 
                            enc_positionwise_conv_kernel_size=args.enc_positionwise_conv_kernel_size,
                            enc_macaron_style=args.enc_macaron_style, 
                            enc_pos_enc_layer_type=args.enc_pos_enc_layer_type, 
                            enc_selfattention_layer_type=args.enc_selfattention_layer_type,
                            enc_activation_type=args.enc_activation_type, 
                            enc_use_cnn_module=args.enc_use_cnn_module, 
                            enc_cnn_module_kernel=args.enc_cnn_module_kernel, 
                            enc_padding_idx=args.enc_padding_idx,

                            output_dim=args.feat_dim,
                            dec_nhead=args.dec_nhead,
                            dec_num_block=args.dec_num_block,
                            n_mels=args.n_mels,
                            double_mel_loss=args.double_mel_loss,
                            local_gaussian=args.local_gaussian,
                            dec_dropout=args.dec_dropout,
                            device=device)
    else:
        raise ValueError('Not Support Model Type %s' % args.model_type)
    print(model)
    print(f'The model has {count_parameters(model):,} trainable parameters')

    # Load model weights
    print("Loading pretrained weights from {}".format(args.model_file))
    checkpoint = torch.load(args.model_file, map_location=device)
    state_dict = checkpoint['state_dict']
    model_dict = model.state_dict()
    state_dict_new = {}
    para_list = []
    # print(model_dict)
    for k, v in state_dict.items():
        # assert k in model_dict
        if k == "normalizer.mean" or k == "normalizer.std" or k == "mel_normalizer.mean" or k == "mel_normalizer.std":
            continue
        if model_dict[k].size() == state_dict[k].size():
            state_dict_new[k] = v
            # print(k)
        else:
            para_list.append(k)

    print("Total {} parameters, loaded {} parameters".format(len(state_dict), len(state_dict_new)))

    if len(para_list) > 0:
        print("Not loading {} because of different sizes".format(", ".join(para_list)))
    # model_dict.update(state_dict_new)
    # model.load_state_dict(model_dict)
    model.load_state_dict(state_dict_new)
    print("Loaded checkpoint {}".format(args.model_file))
    model = model.to(device)
    model.eval()
    

    # Decode
    test_set = SVSDataset(align_root_path=args.test_align,
                           pitch_beat_root_path=args.test_pitch,
                           wav_root_path=args.test_wav,
                           char_max_len=args.char_max_len,
                           max_len=args.num_frames,
                           sr=args.sampling_rate,
                           preemphasis=args.preemphasis,
                           nfft=args.nfft,
                           frame_shift=args.frame_shift,
                           frame_length=args.frame_length,
                           n_mels=args.n_mels,
                           power=args.power,
                           max_db=args.max_db,
                           ref_db=args.ref_db,
                           standard=args.standard,
                           sing_quality=args.sing_quality)
    collate_fn_svs = SVSCollator(args.num_frames, args.char_max_len, args.use_asr_post, args.phone_size)
    test_loader = torch.utils.data.DataLoader(dataset=test_set,
                                               batch_size=1,
                                               shuffle=False,
                                               num_workers=args.num_workers,
                                               collate_fn=collate_fn_svs,
                                               pin_memory=True)

    if args.loss == "l1":
        criterion = MaskedLoss("l1")
    elif args.loss == "mse":
        criterion = MaskedLoss("mse")
    else:
        raise ValueError("Not Support Loss Type")

    losses = AverageMeter()
    spec_losses = AverageMeter()
    if args.perceptual_loss > 0:
        pe_losses = AverageMeter()
    if args.n_mels > 0:
        mel_losses = AverageMeter()
        mcd_metric = AverageMeter()
        f0_distortion_metric, vuv_error_metric = AverageMeter(), AverageMeter()
        if args.double_mel_loss:
            double_mel_losses = AverageMeter()
    model.eval()

    if not os.path.exists(args.prediction_path):
        os.makedirs(args.prediction_path)

    f0_ground_truth_all = np.reshape(np.array([]), (-1,1))
    f0_synthesis_all = np.reshape(np.array([]), (-1,1))
    start_t_test = time.time()

    with torch.no_grad():
        for step, (phone, beat, pitch, spec, real, imag, length, chars, char_len_list, mel) in enumerate(test_loader, 1):
            # if step >= args.decode_sample:
            #     break
            phone = phone.to(device)
            beat = beat.to(device)
            pitch = pitch.to(device).float()
            spec = spec.to(device).float()
            mel = mel.to(device).float()
            real = real.to(device).float()
            imag = imag.to(device).float()
            length_mask = length.unsqueeze(2)
            length_mel_mask = length_mask.repeat(1, 1, mel.shape[2]).float()
            length_mask = length_mask.repeat(1, 1, spec.shape[2]).float()
            length_mask = length_mask.to(device)
            length_mel_mask = length_mel_mask.to(device)
            length = length.to(device)
            char_len_list = char_len_list.to(device)

            if not args.use_asr_post:
                chars = chars.to(device)
                char_len_list = char_len_list.to(device)
            else:
                phone = phone.float()
            
            if args.model_type == "GLU_Transformer":
                output, att, output_mel, output_mel2 = model(chars, phone, pitch, beat, pos_char=char_len_list,
                        pos_spec=length)
            elif args.model_type == "LSTM":
                output, hidden, output_mel, output_mel2 = model(phone, pitch, beat)  
                att = None
            elif args.model_type == "GRU_gs":
                output, att, output_mel = model(spec, phone, pitch, beat, length, args)
                att = None
            elif args.model_type == "PureTransformer":
                output, att, output_mel, output_mel2 = model(chars, phone, pitch, beat, pos_char=char_len_list,
                        pos_spec=length)
            elif args.model_type == "Conformer":
                output, att, output_mel, output_mel2 = model(chars, phone, pitch, beat, pos_char=char_len_list,
                            pos_spec=length)

            spec_origin = spec.clone()
            # spec_origin = spec
            if args.normalize:   
                sepc_normalizer = GlobalMVN(args.stats_file)
                mel_normalizer = GlobalMVN(args.stats_mel_file)
                spec,_ = sepc_normalizer(spec,length)
                mel,_ = mel_normalizer(mel,length)

            spec_loss = criterion(output, spec, length_mask)
            if args.n_mels > 0:
                mel_loss = criterion(output_mel, mel, length_mel_mask)
            else:
                mel_loss = 0

            final_loss = mel_loss + spec_loss

            losses.update(final_loss.item(), phone.size(0))
            spec_losses.update(spec_loss.item(), phone.size(0))
            if args.n_mels > 0:
                mel_losses.update(mel_loss.item(), phone.size(0))

            ### normalize inverse stage
            if args.normalize and args.stats_file:
                output,_ = sepc_normalizer.inverse(output,length)
                # spec,_ = sepc_normalizer.inverse(spec,length)
            
            mcd_value, length_sum = Metrics.Calculate_melcd_fromLinearSpectrum(output, spec_origin, length, args)
            f0_distortion_value, voiced_frame_number_step, vuv_error_value, frame_number_step, f0_ground_truth_step, f0_synthesis_step \
                                = Metrics.Calculate_f0RMSE_VUV_CORR_fromWav(output, spec_origin, length, args, "test")
            f0_ground_truth_all = np.concatenate((f0_ground_truth_all, f0_ground_truth_step), axis=0)
            f0_synthesis_all = np.concatenate((f0_synthesis_all, f0_synthesis_step), axis=0)

            mcd_metric.update(mcd_value, length_sum)
            f0_distortion_metric.update(f0_distortion_value, voiced_frame_number_step)
            vuv_error_metric.update(vuv_error_value, frame_number_step)

            if step % 1 == 0:
                log_figure(step, output, spec_origin, att, length, args.prediction_path, args)
                out_log = "step {}: train_loss {:.4f}; spec_loss {:.4f}; mcd_value {:.4f}; ".format(step,
                                                                        losses.avg, spec_losses.avg, mcd_metric.avg)
                if args.perceptual_loss > 0:
                    out_log += " pe_loss {:.4f}; ".format(pe_losses.avg)
                if args.n_mels > 0:
                    out_log += " mel_loss {:.4f}; ".format(mel_losses.avg)
                    if args.double_mel_loss:
                        out_log += " dmel_loss {:.4f}; ".format(double_mel_losses.avg)
                end = time.time()
                print("{} -- sum_time: {}s".format(out_log, (end-start_t_test)))

    end_t_test = time.time()

    out_log = 'Test Stage: '
    out_log += 'spec_loss: {:.4f} '.format(spec_losses.avg)
    if args.n_mels > 0:
        out_log += 'mel_loss: {:.4f}, '.format(mel_losses.avg)
    # if args.perceptual_loss > 0:
    #     out_log += 'pe_loss: {:.4f}, '.format(train_info['pe_loss'])

    f0_corr = Metrics.compute_f0_corr(f0_ground_truth_all, f0_synthesis_all)

    out_log += "\n\t mcd_value {:.4f} dB ".format(mcd_metric.avg)
    out_log += " f0_rmse_value {:.4f} Hz, vuv_error_value {:.4f} %, F0_CORR {:.4f}; ".format(np.sqrt(f0_distortion_metric.avg), 
                                                                                        vuv_error_metric.avg*100, f0_corr)
    print("{} time: {:.2f}s".format(out_log, end_t_test - start_t_test))