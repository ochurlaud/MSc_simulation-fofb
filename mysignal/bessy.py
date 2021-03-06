#!/usr/bin/env python3
# -*- coding: utf-8 -*-

""" Bessy Signal module

@author: Olivier CHURLAUD <olivier.churlaud@helmholtz-berlin.de>
"""
from __future__ import division, print_function

import math
import matplotlib.pyplot as plt
import numpy as np
import scipy.linalg
import scipy.signal as signal


import mysignal as ms
import search_kicks.tools as sktools


def corrector_order1():
    """ Create a transfer function of shape

    .. math::
        H = \\frac{A}{1 + a \cdot s}

    from measured frequency response.
    """
    A = 3
    a = A / (2*np.pi*3)
    return ms.TF([A], [a, 1])


def simulate(d_s, K, S, H_lp, H_dip, H_ring, delay=0, fs=1, plot=False):
    """
    The bloc diagram is the following:
    ::
                        . . . . . . . . . . .
                        . mBox              .
                        . Fs=150Hz          .
                        .                   .                              | d
     r=0        +----+  . +----+ e  +-----+ . u +-------+ ud  +------+  y  v    +-------+
      ---->(+)->| -\ |--->| S* |--->| K   |---->| delay |---->| Hdip |--->(+)-->| Hring |--+--> orbit
          - ^   +----+  . +----+    +-----+ . - +-------+     +------+       yd +-------+  |
            |           . . . . . . . . . . .                                              |
            |                                                                              |
            +------------------------------------------------------------------------------+
               orbit

       ---Real-time----> <--Sampled-time---> <--Real-time -------
    """

    f_ratio = 10
    fs_real = f_ratio*fs
    Ts_real = 1/fs_real
    Ts = 1/fs
    t_real = np.arange(0, f_ratio*d_s.size) / fs_real
    t = np.arange(0, d_s.size) / fs
    delay_offset = math.ceil(delay*fs_real)

    BPM_nb = S.shape[0]
    CM_nb = S.shape[1]

    svd_nb = min(S.shape[0], min(S.shape[1], 48))
    S_inv = sktools.maths.inverse_with_svd(S, svd_nb)

    # Init real time variables
    r = 0
    y = np.zeros((CM_nb, t_real.size))
    yd = np.zeros((CM_nb, t_real.size))
    orbit = np.zeros((BPM_nb, t_real.size))
    u = np.zeros((CM_nb, t_real.size))
    u_delay = np.zeros((CM_nb, t_real.size))
    d = np.zeros(t_real.size)
    e = np.zeros((CM_nb, t_real.size))

    # Init sample time variables

    du_s = np.zeros((CM_nb, t.size))
    e_s = np.zeros((CM_nb, t.size))

    xring = np.zeros(CM_nb*(H_ring.den.size-1))
    xcor = np.zeros(CM_nb*(H_dip.den.size-1))
    xlp = np.zeros(BPM_nb*(H_lp.den.size-1))
    xk = np.zeros(CM_nb*(K.den.size-1))

    sample = 0
    for k in range(1, t_real.size):
        d[k] = d_s[sample]

        # S* x delta_x
        dorbit, xlp = H_lp.apply_f(r-orbit[:, k-1], xlp, Ts_real)

        if t_real[k] >= t[sample] and sample < t.size-1:
            sample += 1
            e_s[:, sample] = S_inv.dot(dorbit).reshape(CM_nb)

            du_s[:, sample], xk = K.apply_f(e_s[:, sample], xk, Ts)

        # Correction sent to PS
        e[:, k] = e_s[:, sample]
        u[:, k] = du_s[:, sample]

        # Time for computation/PS
        if k >= delay_offset:
            u_delay[:, k] = u[:, k-delay_offset]

        # Corrector magnet propagation
        y[:, k], xcor = H_dip.apply_f(u_delay[:, k], xcor, Ts_real)
        yd[:, k] = y[:, k] + d[k]

        # Response of the ring
        normalized_orbit, xring = H_ring.apply_f(yd[:, k], xring, Ts_real)
        orbit[:, k] = S.dot(normalized_orbit).reshape(BPM_nb)

    if plot:
        idx = np.argmax(np.linalg.norm(orbit, axis=1))
        plt.figure(4,3)
        plt.plot(t_real, d.T, label='perturbation')
        plt.plot(t_real, u[0, :].T, '-m', label='command (PID)')
        plt.plot(t_real, u_delay[0, :].T, '--c', label='delayed command (PID)')
        plt.plot(t_real, yd[0, :].T, '-r', label='output')
        plt.plot(t_real, orbit[idx, :].T, '-k', label='orbit')

        plt.legend(loc='best')
        plt.title('Simulation result')

    return yd, d, fs_real


def toeplitz_block(col, row=None):
    if row is None:
        row = [col[0]]
        for elem in col:
            row.append(elem.conjugate())
    if len(col) != len(row):
        raise ValueError("Both args must have same length")
    if not np.all(col[0] == row[0]):
        raise ValueError("Both args must have same 1st element")
    shape = None
    for k in range(len(col)):
        if shape is not None:
            if col[k].shape != shape or row[k].shape != shape:
                raise ValueError("All elements must have same shape, given: "
                                 "{} and {}".format(row[k].shape, col[k].shape))
        shape = col[k].shape

    for elem in row:
        if elem.shape != shape:
            raise ValueError("All elements must have same shape")
        shape = elem.shape

    arraylist = [col[0]] * len(col)
    A = scipy.linalg.block_diag(*arraylist)

    for k in range(1, len(row)):
        print(k)
        arraylist = [col[k]] * (len(col)-k)
        A[k*shape[0]:, :-k*shape[1]] += scipy.linalg.block_diag(*arraylist)
#        A[k*shape[0]:, :-k*shape[1]] += np.kron(np.eye(len(col)-k), col[k])

#        arraylist = [row[k]] * (len(row)-k)
#        A[:-k*shape[0], k*shape[1]:] += scipy.linalg.block_diag(*arraylist)
#        A[:-k*shape[0], k*shape[1]:] += np.kron(np.eye(len(row)-k), row[k])

    return A


def control_toeplitz(H, Ts, N):
    if H.num.size == 1 and H.den.size == 1:
        return np.eye(N)*H.num[0]/H.den[0]

    A, B, C, D, _ = signal.cont2discrete((H.A, H.B, H.C, H.D), Ts)
    col = [D, C]
    for k in range(2, N):
        col.append(col[k-1].dot(A))
    for k in range(1, N):
        col[k] = col[k].dot(B)
    row = [D] + [np.zeros(D.shape)] * (N-1)
    print("toeplitz ready")
    return toeplitz_block(col, row)


def decimate(N_in, N_out):
    M = np.zeros(N_out, N_in)
    for k in range(N_out):
        M[k, (N_in//N_out)*k] = 1
    return M


def interpol(N_in, N_out):
    ratio = N_out//N_in
    M = np.zeros(N_out, N_in)

    for k in range(N_in):
        M[k*ratio:(k+1)*ratio, k] = np.ones(ratio)
    return M


def real_perturbation(t):
    N = t.size
    Fs = 1/(t[1]-t[0])
    freqs = np.fft.fftfreq(N, 1/Fs)
    freqs_half = freqs[:N//2+1]
    cm_fft = 5*np.random.random(N//2+1)*np.exp(1j*2*np.pi*np.random.random(N//2+1))

    idxmin = np.argmin(abs(freqs_half - 9))
    idx20 = np.argmin(abs(freqs_half - 20))
    for k in range(idxmin, idx20):
        cm_fft[k] = 0.1*cm_fft[k]*(5 - (freqs_half[k] - 11)*(freqs_half[k] - 20))

    nprand = np.random.random
    cmph10 = 2*np.pi*nprand()
    cm_fft[np.argmin(abs(freqs_half - 0))] = 0
    cm_fft[np.argmin(abs(freqs_half - 10))] = 20*np.exp(1j*cmph10)
    cm_fft[np.argmin(abs(freqs_half - 50))] = 30*np.exp(1j*2*np.pi*nprand())
    cm_fft[-1] = 0

    cm_fft = np.concatenate((cm_fft[:-1], np.flipud(cm_fft.conjugate())[:-1]))
    cm_fft *= N/2/np.max(np.abs(cm_fft))
    cm = np.fft.ifft(cm_fft).real

    return cm
