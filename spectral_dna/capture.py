"""
HackRF One Pro capture engine.

Provides a unified IQ capture interface used by all protocol modules.
Supports two backends:
  1. SoapySDR (full Python API)
  2. hackrf_transfer CLI (uses libhackrf directly — more reliable on Windows)

Auto-detects which backend is available and selects the best one.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# Default hackrf_transfer location (radioconda install)
_HACKRF_TRANSFER_SEARCH = [
    os.path.expanduser("~/radioconda/Library/bin/hackrf_transfer.exe"),
    os.path.expanduser("~/radioconda/Library/bin/hackrf_transfer"),
]


def _find_hackrf_transfer() -> str | None:
    """Locate hackrf_transfer executable."""
    # Check PATH first
    found = shutil.which("hackrf_transfer")
    if found:
        return found
    # Check known locations
    for path in _HACKRF_TRANSFER_SEARCH:
        if os.path.isfile(path):
            return path
    return None


# ---------------------------------------------------------------------------
# Capture result container
# ---------------------------------------------------------------------------

@dataclass
class CaptureResult:
    """IQ capture result with metadata."""
    samples: np.ndarray          # complex64 IQ array
    center_freq: float           # Hz
    sample_rate: float           # samples/sec
    bandwidth: float             # Hz
    gain: float                  # dB
    timestamp: float = 0.0       # epoch seconds
    duration: float = 0.0        # actual capture duration (s)
    num_samples: int = 0

    def __post_init__(self):
        self.num_samples = len(self.samples)


# ---------------------------------------------------------------------------
# hackrf_transfer CLI capture engine (primary for Windows)
# ---------------------------------------------------------------------------

class HackRFCapture:
    """
    Capture engine using hackrf_transfer CLI.

    hackrf_transfer writes interleaved int8 I/Q pairs to a file.
    We invoke it per-capture, read the raw file, and convert to complex64.
    Falls back to SoapySDR if hackrf_transfer is not found.
    """

    def __init__(self):
        self._backend = None       # "cli" or "soapy"
        self._exe = None           # path to hackrf_transfer
        self._sdr = None           # SoapySDR device (if using soapy backend)
        self._tmpdir = None

    def open(self):
        """Find and validate HackRF access."""
        # Try CLI backend first (more reliable on Windows)
        exe = _find_hackrf_transfer()
        if exe:
            log.info("Using hackrf_transfer backend: %s", exe)
            # Verify device is present
            hackrf_info = exe.replace("hackrf_transfer", "hackrf_info")
            try:
                result = subprocess.run(
                    [hackrf_info], capture_output=True, text=True, timeout=5,
                )
                if "Found HackRF" in result.stdout:
                    self._backend = "cli"
                    self._exe = exe
                    self._tmpdir = tempfile.mkdtemp(prefix="spectral_dna_")
                    log.info("HackRF detected via hackrf_info")
                    return
                else:
                    log.warning("hackrf_info did not find device: %s", result.stdout)
            except Exception as e:
                log.warning("hackrf_info failed: %s", e)

        # Try SoapySDR backend
        try:
            self._open_soapy()
            return
        except Exception as e:
            log.warning("SoapySDR backend failed: %s", e)

        raise RuntimeError(
            "No HackRF backend available. Install hackrf tools or SoapySDR.\n"
            "  hackrf_transfer: not found\n"
            "  SoapySDR: not available"
        )

    def _open_soapy(self):
        """Open HackRF via SoapySDR."""
        import SoapySDR
        env_path = os.environ.get("PATH", "")
        lib_bin = os.path.expanduser("~/radioconda/Library/bin")
        if lib_bin not in env_path:
            os.environ["PATH"] = lib_bin + os.pathsep + env_path

        results = SoapySDR.Device.enumerate({"driver": "hackrf"})
        if not results:
            raise RuntimeError("SoapySDR found no HackRF devices")
        self._sdr = SoapySDR.Device(dict(results[0]))
        self._backend = "soapy"
        log.info("Opened HackRF via SoapySDR")

    def close(self):
        """Release resources."""
        if self._backend == "soapy" and self._sdr is not None:
            self._sdr = None
        if self._tmpdir and os.path.isdir(self._tmpdir):
            import shutil as _shutil
            _shutil.rmtree(self._tmpdir, ignore_errors=True)
        log.info("HackRF closed")

    def capture(
        self,
        duration: float,
        center_freq: float | None = None,
        sample_rate: float = 2e6,
        bandwidth: float | None = None,
        gain: float = 40.0,
    ) -> CaptureResult:
        """
        Capture IQ samples for *duration* seconds.
        Dispatches to the active backend.
        """
        if self._backend == "cli":
            return self._capture_cli(duration, center_freq, sample_rate, bandwidth, gain)
        elif self._backend == "soapy":
            return self._capture_soapy(duration, center_freq, sample_rate, bandwidth, gain)
        else:
            raise RuntimeError("No capture backend initialized. Call open() first.")

    # -- CLI backend --------------------------------------------------------

    def _capture_cli(
        self,
        duration: float,
        center_freq: float | None,
        sample_rate: float,
        bandwidth: float | None,
        gain: float,
    ) -> CaptureResult:
        """Capture via hackrf_transfer CLI."""
        if center_freq is None:
            raise ValueError("center_freq is required")

        # HackRF frequency range: 1 MHz – 6 GHz
        if center_freq < 1e6 or center_freq > 6e9:
            log.warning(
                "Frequency %.3f MHz outside HackRF range (1 MHz – 6 GHz), returning empty",
                center_freq / 1e6,
            )
            return CaptureResult(
                samples=np.array([], dtype=np.complex64),
                center_freq=center_freq, sample_rate=sample_rate,
                bandwidth=bandwidth or sample_rate, gain=gain,
                timestamp=time.time(), duration=0.0,
            )

        # HackRF minimum sample rate: 2 Msps
        sample_rate = max(sample_rate, 2e6)

        num_samples = int(sample_rate * duration)
        # hackrf_transfer -n is in number of samples (I+Q pairs = bytes/2)
        # File format: interleaved int8 I, Q
        raw_path = os.path.join(self._tmpdir, f"cap_{int(center_freq)}_{int(time.time())}.raw")

        # Build command
        # -r file  -f freq_hz  -s sample_rate  -n num_samples  -l lna_gain  -g vga_gain
        lna_gain = min(int(gain), 40)
        vga_gain = min(int(gain), 62)

        cmd = [
            self._exe,
            "-r", raw_path,
            "-f", str(int(center_freq)),
            "-s", str(int(sample_rate)),
            "-n", str(num_samples),
            "-l", str(lna_gain),
            "-g", str(vga_gain),
        ]

        log.info(
            "Capturing %.3f MHz @ %.2f Msps for %.2f s (%d samples)",
            center_freq / 1e6, sample_rate / 1e6, duration, num_samples,
        )

        t_start = time.time()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=duration + 10,
        )
        elapsed = time.time() - t_start

        if result.returncode != 0 and "Exiting" not in result.stderr:
            log.error("hackrf_transfer stderr: %s", result.stderr)

        # Parse average power from hackrf_transfer output
        for line in result.stderr.splitlines():
            if "average power" in line.lower() or "dBfs" in line.lower():
                log.info("  %s", line.strip())

        # Read raw int8 IQ file
        if not os.path.isfile(raw_path):
            log.error("Capture file not created: %s", raw_path)
            return CaptureResult(
                samples=np.array([], dtype=np.complex64),
                center_freq=center_freq,
                sample_rate=sample_rate,
                bandwidth=bandwidth or sample_rate,
                gain=gain,
                timestamp=t_start,
                duration=elapsed,
            )

        raw = np.fromfile(raw_path, dtype=np.int8)
        os.remove(raw_path)

        # Convert interleaved int8 I,Q to complex64 (normalized to ±1.0)
        iq = raw.astype(np.float32) / 128.0
        samples = iq[0::2] + 1j * iq[1::2]
        samples = samples.astype(np.complex64)

        log.info("Captured %d samples in %.2f s", len(samples), elapsed)

        return CaptureResult(
            samples=samples,
            center_freq=center_freq,
            sample_rate=sample_rate,
            bandwidth=bandwidth or sample_rate,
            gain=gain,
            timestamp=t_start,
            duration=elapsed,
        )

    # -- SoapySDR backend ---------------------------------------------------

    def _capture_soapy(
        self,
        duration: float,
        center_freq: float | None,
        sample_rate: float,
        bandwidth: float | None,
        gain: float,
    ) -> CaptureResult:
        """Capture via SoapySDR Python API."""
        from SoapySDR import SOAPY_SDR_CF32, SOAPY_SDR_RX

        sdr = self._sdr
        sdr.setSampleRate(SOAPY_SDR_RX, 0, sample_rate)
        if center_freq is not None:
            sdr.setFrequency(SOAPY_SDR_RX, 0, center_freq)
        sdr.setBandwidth(SOAPY_SDR_RX, 0, bandwidth or sample_rate)
        sdr.setGain(SOAPY_SDR_RX, 0, "LNA", min(gain, 40.0))
        sdr.setGain(SOAPY_SDR_RX, 0, "VGA", min(gain, 62.0))

        actual_rate = sdr.getSampleRate(SOAPY_SDR_RX, 0)
        actual_freq = sdr.getFrequency(SOAPY_SDR_RX, 0)
        num_samples = int(actual_rate * duration)

        stream = sdr.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32)
        sdr.activateStream(stream)

        samples = np.empty(num_samples, dtype=np.complex64)
        buf = np.empty(65536, dtype=np.complex64)
        ptr = 0
        t_start = time.time()

        while ptr < num_samples:
            chunk = min(65536, num_samples - ptr)
            sr = sdr.readStream(stream, [buf], chunk)
            if sr.ret > 0:
                samples[ptr : ptr + sr.ret] = buf[: sr.ret]
                ptr += sr.ret
            elif sr.ret == -1:
                continue

        elapsed = time.time() - t_start
        sdr.deactivateStream(stream)
        sdr.closeStream(stream)

        return CaptureResult(
            samples=samples[:ptr],
            center_freq=actual_freq,
            sample_rate=actual_rate,
            bandwidth=bandwidth or actual_rate,
            gain=gain,
            timestamp=t_start,
            duration=elapsed,
        )


# ---------------------------------------------------------------------------
# DSP utility helpers shared across protocol modules
# ---------------------------------------------------------------------------

def estimate_cfo(samples: np.ndarray, repeat_len: int, sample_rate: float) -> float:
    """
    Estimate carrier frequency offset from cyclic-prefix / preamble
    autocorrelation.  Returns CFO in Hz.

    *repeat_len* is the periodicity in samples (e.g. 16 for 802.11 STF).
    """
    conj_product = samples[repeat_len:] * np.conj(samples[:-repeat_len])
    avg_angle = np.angle(np.mean(conj_product))
    cfo_hz = avg_angle * sample_rate / (2.0 * np.pi * repeat_len)
    return float(cfo_hz)


def estimate_iq_imbalance(samples: np.ndarray) -> tuple[float, float]:
    """
    Estimate IQ gain and phase imbalance from raw IQ.

    Returns (gain_imbalance_dB, phase_imbalance_deg).
    """
    i = samples.real
    q = samples.imag
    power_i = np.mean(i ** 2)
    power_q = np.mean(q ** 2)
    gain_imbalance_db = 10.0 * np.log10(power_i / max(power_q, 1e-20))
    cross = np.mean(i * q)
    phase_imbalance_rad = np.arcsin(
        np.clip(cross / np.sqrt(max(power_i * power_q, 1e-40)), -1.0, 1.0)
    )
    return float(gain_imbalance_db), float(np.degrees(phase_imbalance_rad))


def compute_power_spectral_density(
    samples: np.ndarray, sample_rate: float, nfft: int = 1024
) -> tuple[np.ndarray, np.ndarray]:
    """Return (frequencies_hz, psd_dBm_Hz) arrays."""
    from scipy.signal import welch

    freqs, psd = welch(samples, fs=sample_rate, nperseg=nfft, return_onesided=False)
    psd_dbm = 10.0 * np.log10(np.maximum(psd, 1e-20)) + 30  # dBm/Hz
    return freqs, psd_dbm


def envelope_detect(samples: np.ndarray, cutoff_ratio: float = 0.05) -> np.ndarray:
    """AM envelope detection: magnitude + low-pass filter."""
    from scipy.signal import butter, sosfilt

    env = np.abs(samples)
    sos = butter(4, cutoff_ratio, btype="low", output="sos")
    return sosfilt(sos, env)


def fm_demodulate(samples: np.ndarray) -> np.ndarray:
    """Instantaneous-frequency FM demodulation."""
    return np.angle(samples[1:] * np.conj(samples[:-1]))


def clock_recover_mm(bits_soft: np.ndarray, samples_per_symbol: float) -> np.ndarray:
    """
    Mueller-and-Muller clock recovery.
    Returns hard-decision bit array.
    """
    mu = 0.0
    gain_mu = 0.175
    omega = samples_per_symbol
    gain_omega = 0.25 * gain_mu * gain_mu
    omega_mid = omega
    omega_lim = omega * 0.01

    recovered = []
    idx = 0
    last_sample = 0.0
    last_decision = 0

    while idx < len(bits_soft) - 1:
        sample = np.interp(idx + mu, np.arange(len(bits_soft)), bits_soft)
        decision = 1 if sample > 0 else -1
        recovered.append(decision)

        # timing error
        error = (sample - last_sample) * last_decision - (sample - last_sample) * decision
        error = max(-1.0, min(1.0, error))

        last_sample = sample
        last_decision = decision

        omega = omega + gain_omega * error
        omega = max(omega_mid - omega_lim * 50, min(omega_mid + omega_lim * 50, omega))
        mu = mu + omega + gain_mu * error
        idx_new = int(idx + mu)
        mu = mu - (idx_new - idx)
        idx = idx_new

    return np.array([(1 if b > 0 else 0) for b in recovered], dtype=np.uint8)
