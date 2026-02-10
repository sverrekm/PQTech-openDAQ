"""
Strøm Analysator Demo - PQTECH
================================
Dette eksemplet demonstrerer:
- Simulering av AC strømmåling (50 Hz)
- FFT-analyse for frekvensinnhold
- Harmonisk analyse (THD beregning)
- RMS-beregning
- Visualisering av data
- Logging til CSV

Kan utvides til å bruke reelt DAQ-utstyr ved å endre connection string.
"""

import sys
import os
import time
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import csv

# Add openDAQ to path
build_path = os.path.join(os.path.dirname(__file__), 'build', 'bin', 'Release')
sys.path.insert(0, build_path)

import opendaq as daq

class StromAnalysator:
    """Klasse for strømanalyse med openDAQ"""

    def __init__(self, sample_rate=10000, nett_frekvens=50):
        """
        Initialiserer Strøm Analysator

        Args:
            sample_rate: Samplingsfrekvens i Hz (standard 10 kHz)
            nett_frekvens: Forventet nettfrekvens i Hz (50 Hz i Norge)
        """
        self.sample_rate = sample_rate
        self.nett_frekvens = nett_frekvens
        self.instance = None
        self.device = None
        self.reader = None

    def connect(self, connection_string="auto"):
        """
        Kobler til DAQ-enhet

        Args:
            connection_string: Connection string for DAQ ("auto" for simulator)
        """
        print("=" * 70)
        print("PQTECH Strøm Analysator - Initialisering")
        print("=" * 70)

        # Opprett instance
        print("\n[1/4] Oppretter openDAQ instance...")
        self.instance = daq.Instance()
        print("      [OK] Instance opprettet")

        # Finn tilgjengelige enheter
        print("\n[2/4] Søker etter tilgjengelige enheter...")
        available = self.instance.available_devices
        print(f"      Funnet {len(available)} enheter:")
        for dev in available:
            print(f"      - {dev.name}: {dev.connection_string}")

        # Koble til enhet
        print("\n[3/4] Kobler til enhet...")
        if connection_string == "auto":
            # Bruk simulator for demo
            sim = [d for d in available if 'simulator' in d.name.lower()][0]
            connection_string = sim.connection_string

        self.device = self.instance.add_device(connection_string)
        print(f"      [OK] Koblet til: {self.device.name}")

        # Finn første kanal (antar dette er strømmåling)
        channels = self.device.channels_recursive
        if len(channels) == 0:
            raise RuntimeError("Ingen kanaler funnet på enheten")

        channel = channels[0]

        # Konfigurer kanal hvis mulig
        try:
            # Sett samplingsrate
            if hasattr(channel, 'set_property_value'):
                channel.set_property_value("SampleRate", self.sample_rate)
                print(f"      [OK] Samplingsrate satt til {self.sample_rate} Hz")

                # For simulator: Sett til sinus med 50 Hz
                channel.set_property_value("Waveform", 1)  # Sine wave
                channel.set_property_value("Frequency", self.nett_frekvens)
                channel.set_property_value("Amplitude", 10.0)  # 10A RMS
                print(f"      [OK] Simulator konfigurert: {self.nett_frekvens} Hz, 10A")
        except Exception as e:
            print(f"      [INFO] Kunne ikke konfigurere kanal: {e}")

        # Opprett reader
        print("\n[4/4] Oppretter data reader...")
        signal = channel.signals[0]
        self.reader = daq.StreamReader(signal)
        print("      [OK] Reader opprettet")

        print("\n" + "=" * 70)
        print("Klar for måling!")
        print("=" * 70 + "\n")

    def read_samples(self, num_samples, timeout=5):
        """
        Leser antall samples fra DAQ

        Args:
            num_samples: Antall samples å lese
            timeout: Timeout i sekunder

        Returns:
            numpy array med samples
        """
        # Vent litt for at data skal genereres
        time.sleep(0.1)

        start_time = time.time()
        while True:
            samples = self.reader.read(num_samples)
            if len(samples) >= num_samples:
                return samples[:num_samples]

            if time.time() - start_time > timeout:
                print(f"      [WARNING] Timeout - fikk bare {len(samples)} av {num_samples} samples")
                return samples

            time.sleep(0.1)

    def calculate_rms(self, samples):
        """Beregner RMS (Root Mean Square) verdi"""
        return np.sqrt(np.mean(samples**2))

    def calculate_fft(self, samples):
        """
        Beregner FFT og returnerer frekvenser og magnituder

        Returns:
            freqs: Frekvensarray
            magnitudes: Magnitudearray (normalisert)
        """
        n = len(samples)

        # Utfør FFT
        fft_result = np.fft.fft(samples)

        # Frekvensarray
        freqs = np.fft.fftfreq(n, 1/self.sample_rate)

        # Magnitude (normalisert)
        magnitudes = np.abs(fft_result) * 2 / n

        # Returner bare positive frekvenser
        positive_idx = freqs >= 0
        return freqs[positive_idx], magnitudes[positive_idx]

    def find_harmonics(self, freqs, magnitudes, num_harmonics=10):
        """
        Finner harmoniske komponenter

        Args:
            freqs: Frekvensarray
            magnitudes: Magnitudearray
            num_harmonics: Antall harmoniske å finne

        Returns:
            Liste med (frekvens, magnitude) tupler
        """
        harmonics = []

        # Finn grunnfrekvens (fundamental)
        # Søk rundt forventet nettfrekvens
        search_range = 5  # Hz
        idx_min = np.argmin(np.abs(freqs - (self.nett_frekvens - search_range)))
        idx_max = np.argmin(np.abs(freqs - (self.nett_frekvens + search_range)))

        fundamental_idx = idx_min + np.argmax(magnitudes[idx_min:idx_max])
        fundamental_freq = freqs[fundamental_idx]
        fundamental_mag = magnitudes[fundamental_idx]

        harmonics.append((fundamental_freq, fundamental_mag))

        # Finn harmoniske (2f, 3f, 4f, ...)
        for n in range(2, num_harmonics + 1):
            target_freq = fundamental_freq * n

            # Søk rundt forventet harmonisk
            search_range = 2  # Hz
            idx = np.argmin(np.abs(freqs - target_freq))

            # Finn peak i område
            search_idx = int(search_range * len(freqs) / (freqs[-1]))
            start_idx = max(0, idx - search_idx)
            end_idx = min(len(freqs), idx + search_idx)

            if end_idx > start_idx:
                local_peak_idx = start_idx + np.argmax(magnitudes[start_idx:end_idx])
                harmonics.append((freqs[local_peak_idx], magnitudes[local_peak_idx]))
            else:
                harmonics.append((target_freq, 0))

        return harmonics

    def calculate_thd(self, harmonics):
        """
        Beregner Total Harmonic Distortion (THD)

        Args:
            harmonics: Liste med (frekvens, magnitude) tupler

        Returns:
            THD i prosent
        """
        if len(harmonics) < 2:
            return 0

        fundamental = harmonics[0][1]
        if fundamental == 0:
            return 0

        # Sum av kvadrerte harmoniske (unntatt fundamental)
        harmonic_sum = sum(h[1]**2 for h in harmonics[1:])

        # THD = sqrt(sum of harmonic squares) / fundamental
        thd = np.sqrt(harmonic_sum) / fundamental * 100

        return thd

    def analyze(self, duration=1.0, plot=True, save_csv=True):
        """
        Utfører fullstendig analyse av strømsignal

        Args:
            duration: Varighet av måling i sekunder
            plot: Om resultater skal plottes
            save_csv: Om resultater skal lagres til CSV

        Returns:
            Dictionary med analyseresultater
        """
        print(f"\nStarter analyse ({duration} sekunder)...")
        print("-" * 70)

        # Beregn antall samples
        num_samples = int(self.sample_rate * duration)
        print(f"Leser {num_samples} samples @ {self.sample_rate} Hz...")

        # Les data
        samples = self.read_samples(num_samples)
        print(f"[OK] Leste {len(samples)} samples\n")

        # Analyser
        results = {}

        # 1. RMS
        print("Beregner RMS...")
        results['rms'] = self.calculate_rms(samples)
        print(f"   RMS strøm: {results['rms']:.3f} A")

        # 2. Peak-to-peak
        results['peak_to_peak'] = np.max(samples) - np.min(samples)
        print(f"   Peak-to-peak: {results['peak_to_peak']:.3f} A")

        # 3. FFT
        print("\nUtfører FFT-analyse...")
        freqs, magnitudes = self.calculate_fft(samples)
        results['freqs'] = freqs
        results['magnitudes'] = magnitudes
        print(f"   [OK] FFT beregnet ({len(freqs)} frekvenskomponenter)")

        # 4. Harmoniske
        print("\nFinner harmoniske komponenter...")
        harmonics = self.find_harmonics(freqs, magnitudes, num_harmonics=10)
        results['harmonics'] = harmonics

        print(f"\n   {'Harmonisk':<12} {'Frekvens (Hz)':<15} {'Magnitude (A)':<15} {'Prosent av fundamental'}")
        print(f"   {'-'*60}")
        fundamental_mag = harmonics[0][1]
        for i, (freq, mag) in enumerate(harmonics):
            if i == 0:
                harm_name = "Fundamental"
            else:
                harm_name = f"{i+1}. harmonisk"

            percent = (mag / fundamental_mag * 100) if fundamental_mag > 0 else 0
            print(f"   {harm_name:<12} {freq:>12.2f} Hz {mag:>14.3f} A   {percent:>6.1f}%")

        # 5. THD
        print("\nBeregner Total Harmonic Distortion (THD)...")
        results['thd'] = self.calculate_thd(harmonics)
        print(f"   THD: {results['thd']:.2f}%")

        # Lagre rådata
        results['samples'] = samples
        results['sample_rate'] = self.sample_rate
        results['timestamp'] = datetime.now()

        print("\n" + "-" * 70)
        print("Analyse fullført!\n")

        # Plot
        if plot:
            self.plot_results(results)

        # Lagre til CSV
        if save_csv:
            self.save_to_csv(results)

        return results

    def plot_results(self, results):
        """Plotter analyseresultater"""
        print("Genererer grafer...")

        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        fig.suptitle('PQTECH Strøm Analysator - Resultater', fontsize=16, fontweight='bold')

        # 1. Tidsdomene (første 200ms)
        ax1 = axes[0, 0]
        samples = results['samples']
        time_array = np.arange(len(samples)) / results['sample_rate']
        display_samples = int(0.2 * results['sample_rate'])  # 200ms

        ax1.plot(time_array[:display_samples] * 1000, samples[:display_samples], 'b-', linewidth=1)
        ax1.set_xlabel('Tid (ms)')
        ax1.set_ylabel('Strøm (A)')
        ax1.set_title('Tidsdomene (første 200ms)')
        ax1.grid(True, alpha=0.3)

        # Legg til RMS-info
        ax1.text(0.02, 0.98, f'RMS: {results["rms"]:.3f} A\nPeak-to-peak: {results["peak_to_peak"]:.3f} A',
                transform=ax1.transAxes, verticalalignment='top',
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

        # 2. Frekvensdomene (full spektrum)
        ax2 = axes[0, 1]
        freqs = results['freqs']
        magnitudes = results['magnitudes']

        # Plot bare opp til 1 kHz for klarhet
        max_freq_idx = np.argmin(np.abs(freqs - 1000))
        ax2.plot(freqs[:max_freq_idx], magnitudes[:max_freq_idx], 'r-', linewidth=1)
        ax2.set_xlabel('Frekvens (Hz)')
        ax2.set_ylabel('Magnitude (A)')
        ax2.set_title('Frekvensspektrum (0-1000 Hz)')
        ax2.grid(True, alpha=0.3)
        ax2.set_xlim(0, 1000)

        # 3. Harmoniske komponenter (bar chart)
        ax3 = axes[1, 0]
        harmonics = results['harmonics']
        harm_freqs = [h[0] for h in harmonics[:6]]  # Første 6 harmoniske
        harm_mags = [h[1] for h in harmonics[:6]]

        bars = ax3.bar(range(len(harm_freqs)), harm_mags, color=['blue'] + ['orange']*5)
        ax3.set_xlabel('Harmonisk nummer')
        ax3.set_ylabel('Magnitude (A)')
        ax3.set_title('Harmoniske komponenter')
        ax3.set_xticks(range(len(harm_freqs)))
        ax3.set_xticklabels([f'f0\n{harm_freqs[0]:.1f}Hz'] +
                           [f'{i}f0\n{freq:.1f}Hz' for i, freq in enumerate(harm_freqs[1:], 2)])
        ax3.grid(True, alpha=0.3, axis='y')

        # Legg til THD-info
        ax3.text(0.98, 0.98, f'THD: {results["thd"]:.2f}%',
                transform=ax3.transAxes, verticalalignment='top', horizontalalignment='right',
                bbox=dict(boxstyle='round', facecolor='lightcoral', alpha=0.7),
                fontsize=12, fontweight='bold')

        # 4. Harmonisk distorsjon (normalized)
        ax4 = axes[1, 1]
        fundamental_mag = harmonics[0][1]
        harm_percent = [(h[1] / fundamental_mag * 100) if fundamental_mag > 0 else 0
                       for h in harmonics[1:6]]  # 2. til 6. harmonisk

        bars = ax4.bar(range(2, 7), harm_percent, color='orange')
        ax4.set_xlabel('Harmonisk nummer')
        ax4.set_ylabel('Prosent av fundamental (%)')
        ax4.set_title('Harmonisk distorsjon (% av fundamental)')
        ax4.set_xticks(range(2, 7))
        ax4.grid(True, alpha=0.3, axis='y')

        # Legg til verditekst på hver bar
        for i, bar in enumerate(bars):
            height = bar.get_height()
            ax4.text(bar.get_x() + bar.get_width()/2., height,
                    f'{harm_percent[i]:.1f}%',
                    ha='center', va='bottom', fontsize=9)

        plt.tight_layout()

        # Lagre figur
        timestamp = results['timestamp'].strftime('%Y%m%d_%H%M%S')
        filename = f'strom_analyse_{timestamp}.png'
        plt.savefig(filename, dpi=150, bbox_inches='tight')
        print(f"   [OK] Graf lagret: {filename}")

        plt.show()

    def save_to_csv(self, results):
        """Lagrer resultater til CSV"""
        timestamp = results['timestamp'].strftime('%Y%m%d_%H%M%S')

        # 1. Lagre sammendrag
        summary_file = f'strom_analyse_sammendrag_{timestamp}.csv'
        with open(summary_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['PQTECH Strøm Analysator - Sammendrag'])
            writer.writerow(['Timestamp', results['timestamp'].strftime('%Y-%m-%d %H:%M:%S')])
            writer.writerow([])
            writer.writerow(['Parameter', 'Verdi', 'Enhet'])
            writer.writerow(['RMS strøm', f"{results['rms']:.3f}", 'A'])
            writer.writerow(['Peak-to-peak', f"{results['peak_to_peak']:.3f}", 'A'])
            writer.writerow(['THD', f"{results['thd']:.2f}", '%'])
            writer.writerow(['Samplingsrate', results['sample_rate'], 'Hz'])
            writer.writerow(['Antall samples', len(results['samples']), ''])
            writer.writerow([])
            writer.writerow(['Harmoniske komponenter'])
            writer.writerow(['Harmonisk', 'Frekvens (Hz)', 'Magnitude (A)', '% av fundamental'])

            fundamental_mag = results['harmonics'][0][1]
            for i, (freq, mag) in enumerate(results['harmonics']):
                percent = (mag / fundamental_mag * 100) if fundamental_mag > 0 else 0
                harm_name = 'Fundamental' if i == 0 else f'{i+1}. harmonisk'
                writer.writerow([harm_name, f'{freq:.2f}', f'{mag:.3f}', f'{percent:.1f}'])

        print(f"   [OK] Sammendrag lagret: {summary_file}")

        # 2. Lagre rådata (samples)
        raw_file = f'strom_analyse_rawdata_{timestamp}.csv'
        with open(raw_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Tid (s)', 'Strøm (A)'])
            time_array = np.arange(len(results['samples'])) / results['sample_rate']
            for t, sample in zip(time_array, results['samples']):
                writer.writerow([f'{t:.6f}', f'{sample:.6f}'])

        print(f"   [OK] Rådata lagret: {raw_file}")


def main():
    """Hovedprogram"""
    print("\n")
    print("*" * 70)
    print("*" + " " * 68 + "*")
    print("*" + "  PQTECH Strøm Analysator - Demonstrasjon".center(68) + "*")
    print("*" + " " * 68 + "*")
    print("*" * 70)
    print("\n")

    try:
        # Opprett analysator
        analysator = StromAnalysator(sample_rate=10000, nett_frekvens=50)

        # Koble til
        analysator.connect(connection_string="auto")

        # Utfør analyse
        results = analysator.analyze(
            duration=0.5,  # 0.5 sekunder
            plot=True,
            save_csv=True
        )

        # Vis oppsummering
        print("\n" + "=" * 70)
        print("OPPSUMMERING")
        print("=" * 70)
        print(f"RMS strøm:        {results['rms']:.3f} A")
        print(f"Peak-to-peak:     {results['peak_to_peak']:.3f} A")
        print(f"Fundamental:      {results['harmonics'][0][0]:.2f} Hz")
        print(f"THD:              {results['thd']:.2f}%")
        print("=" * 70)

        print("\n✓ Demo fullført!")
        print("\nNeste steg:")
        print("  1. Erstatt simulator med reelt DAQ-utstyr")
        print("  2. Tilpass parametere (samplingsrate, varighet)")
        print("  3. Legg til automatisk logging og alarmer")
        print("  4. Integrer med database eller web dashboard")

    except Exception as e:
        print(f"\n[ERROR] Feil under kjøring: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
