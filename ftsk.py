"""Single entry point for the FTSK reference implementation."""
import argparse
from importlib import import_module
import sys


TOOLS = {
    'packet': ('modem', 'encode or blind-decode a 32-byte packet, run an AWGN demo'),
    'profile': ('profiles', 'encode or receive four-slot and eight-slot PHY packets'),
    'file': ('transfer', 'encode a binary file to baseband IQ or recover it from IQ'),
    'radio': ('radio', 'send or receive one packet using a SoapySDR device'),
    'radio-file': ('radio_file', 'send or receive a binary file using a SoapySDR device'),
    'capture': ('capture', 'record hardware IQ or run offline wide-frequency acquisition'),
    'link': ('linktest', 'generate numbered test packets and count losses and duplicates'),
}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter,
                                epilog='\n'.join(f'  {k:12} {v[1]}' for k,v in TOOLS.items()))
    p.add_argument('tool', choices=TOOLS)
    args = p.parse_args(sys.argv[1:2])
    module = import_module(TOOLS[args.tool][0])
    # All tools can still run as standalone scripts with their original arguments.
    sys.argv = [f'{sys.argv[0]} {args.tool}', *sys.argv[2:]]
    return module.main()


if __name__ == '__main__':
    raise SystemExit(main())
