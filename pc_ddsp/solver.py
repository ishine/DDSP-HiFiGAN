import os
import time
import numpy as np
import torch

from logger.saver import Saver
from logger import utils

def test(args, model, loss_func, loader_test, saver):
    print(' [*] testing...')
    model.eval()

    # losses
    test_loss = 0.
    test_loss_rss = 0.
    test_loss_uv = 0.
    
    # intialization
    num_batches = len(loader_test)
    rtf_all = []
    
    # run
    with torch.no_grad():
        for bidx, data in enumerate(loader_test):
            fn = data['name'][0]
            print('--------')
            print('{}/{} - {}'.format(bidx, num_batches, fn))

            # unpack data
            for k in data.keys():
                if k != 'name':
                    data[k] = data[k].to(args.device).float()
            print('>>', data['name'][0])

            # forward
            st_time = time.time()
            signal, _, (s_h, s_n) = model(data['mel'], data['f0'])
            ed_time = time.time()

            # crop
            min_len = np.min([signal.shape[1], data['audio'].shape[1]])
            signal        = signal[:,:min_len]
            data['audio'] = data['audio'][:,:min_len]

            # RTF
            run_time = ed_time - st_time
            song_time = data['audio'].shape[-1] / args.data.sampling_rate
            rtf = run_time / song_time
            print('RTF: {}  | {} / {}'.format(rtf, run_time, song_time))
            rtf_all.append(rtf)
           
            # loss
            loss, (loss_rss, loss_uv) = loss_func(signal, s_h, data['audio'], data['uv'])

            test_loss += loss.item()
            test_loss_rss += loss_rss.item()
            test_loss_uv += loss_uv.item()

            # log
            saver.log_audio({fn+'/gt.wav': data['audio'], fn+'/pred.wav': signal})
            
    # report
    test_loss /= num_batches
    test_loss_rss /= num_batches
    test_loss_uv /= num_batches
    
    # check
    print(' [test_loss] test_loss:', test_loss)
    print(' [test_loss_rss] test_loss_rss:', test_loss_rss)
    print(' [test_loss_uv] test_loss_uv:', test_loss_uv)
    print(' Real Time Factor', np.mean(rtf_all))
    return test_loss, test_loss_rss, test_loss_uv


def train(args, initial_global_step, model, optimizer, loss_func, loader_train, loader_test):
    # saver
    saver = Saver(args, initial_global_step=initial_global_step)

    # model size
    params_count = utils.get_network_paras_amount({'model': model})
    saver.log_info('--- model size ---')
    saver.log_info(params_count)
    
    # run
    best_loss = np.inf
    num_batches = len(loader_train)
    model.train()
    saver.log_info('======= start training =======')
    for epoch in range(args.train.epochs):
        for batch_idx, data in enumerate(loader_train):
            saver.global_step_increment()
            optimizer.zero_grad()

            # unpack data
            for k in data.keys():
                if k != 'name':
                    data[k] = data[k].to(args.device)
            
            # forward
            signal, _, (s_h, s_n) = model(data['mel'], data['f0'], infer=False)

            # loss
            detach_uv = False
            if saver.global_step < args.loss.detach_uv_step:
                detach_uv = True
            loss, (loss_rss, loss_uv) = loss_func(signal, s_h, data['audio'], data['uv'], detach_uv = detach_uv, uv_tolerance = args.loss.uv_tolerance)
            
            # handle nan loss
            if torch.isnan(loss):
                raise ValueError(' [x] nan loss ')
            else:
                # backpropagate
                loss.backward()
                optimizer.step()

            # log loss
            if saver.global_step % args.train.interval_log == 0:
                saver.log_info(
                    'epoch: {} | {:3d}/{:3d} | {} | batch/s: {:.2f} | loss: {:.3f} | rss: {:.3f}| uv: {:.3f} | time: {} | step: {}'.format(
                        epoch,
                        batch_idx,
                        num_batches,
                        args.env.expdir,
                        args.train.interval_log/saver.get_interval_time(),
                        loss.item(),
                        loss_rss.item(),
                        loss_uv.item(),
                        saver.get_total_time(),
                        saver.global_step
                    )
                )
                
                saver.log_value({
                    'train/loss': loss.item(),
                    'train/rss': loss_rss.item(),
                    'train/uv': loss_uv.item()
                })
            
            # validation
            if saver.global_step % args.train.interval_val == 0:
                # save latest
                saver.save_model(model, optimizer, postfix=f'{saver.global_step}')

                # run testing set
                test_loss, test_loss_rss, test_loss_uv = test(args, model, loss_func, loader_test, saver)
             
                saver.log_info(
                    ' --- <validation> --- \nloss: {:.3f} | rss: {:.3f} | uv: {:.3f}. '.format(
                        test_loss,
                        test_loss_rss,
                        test_loss_uv
                    )
                )
                
                saver.log_value({
                    'validation/loss': test_loss,
                    'validation/rss': test_loss_rss,
                    'validation/uv': test_loss_uv
                })
                model.train()

                # save best model
                if test_loss < best_loss:
                    saver.log_info(' [V] best model updated.')
                    saver.save_model(model, optimizer, postfix='best')
                    best_loss = test_loss

                          
