"""Adversarial filesystem cases in a private temporary directory (no real device I/O)."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import sys
import tempfile
from unittest import TestCase
from unittest.mock import patch

from capture import main as capture
from frame import encode_frame
from linktest import main as link
import modem
import profiles


class SafeOutputTest(TestCase):
    def test_packet_and_profile_refuse_alias_even_with_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)/'payload'
            src.write_bytes(bytes(range(32)))
            alias = Path(tmp)/'alias'
            os.link(src,alias)
            for module in (modem, profiles):
                for option in ([],['--force']):
                    with self.subTest(module=module.__name__,option=option), patch.object(sys,'argv',['test','encode',str(src),str(alias),*option]), redirect_stderr(StringIO()):
                        with self.assertRaises((SystemExit,ValueError)):
                            module.main()
                        self.assertEqual(src.read_bytes(),bytes(range(32)))
                        self.assertTrue(src.samefile(alias))

    def test_no_partial_link_probes(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/'traces'
            with patch('linktest.encode_frame', side_effect=[encode_frame(bytes(32)), OSError(28,'injected write error')]), redirect_stderr(StringIO()):
                self.assertNotEqual(link(['generate','--count','2','--iq',str(dest)]),0)
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_iq_publish_fsync_failure_and_default_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            src,dst=Path(tmp)/'p',Path(tmp)/'iq'
            src.write_bytes(bytes(32)); dst.write_bytes(b'previous')
            for options in ([],['--force']):
                with patch.object(sys,'argv',['test','encode',str(src),str(dst),*options]), patch('io_helpers.os.fsync',side_effect=OSError(28,'injected fsync error')), redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit):
                        modem.main()
                self.assertEqual(dst.read_bytes(),b'previous')
                self.assertEqual(set(Path(tmp).iterdir()),{src,dst})

    def test_malformed_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            iq=Path(tmp)/'test.cf32'
            iq.write_bytes(b'')
            for bad in ('[]','{"rate_hz": true}','{"samples": -1}'):
                Path(str(iq)+'.json').write_text(bad)
                with redirect_stderr(StringIO()) as output:
                    self.assertEqual(capture(['replay',str(iq),'--rate','1024000']),1)
                self.assertIn('metadata',output.getvalue())
