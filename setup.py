from setuptools import setup, find_packages

setup(
    name="spectral_dna",
    version="1.0.0",
    description="RF emission fingerprinting tool for HackRF One Pro",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "numpy>=1.24.0",
        "scipy>=1.11.0",
        "rich>=13.0.0",
        "click>=8.1.0",
        "scapy>=2.5.0",
        "Jinja2>=3.1.0",
    ],
    extras_require={
        "sdr": ["SoapySDR"],
    },
    entry_points={
        "console_scripts": [
            "spectral-dna=spectral_dna.__main__:main",
        ],
    },
)
