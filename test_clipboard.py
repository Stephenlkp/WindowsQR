import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import app
from windows_clipboard import clipboard_formats


class ClipboardTests(unittest.TestCase):
    def test_formats_include_file_paths_and_image_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.png'
            Image.new('RGB', (3, 2), 'red').save(path)
            formats = dict(clipboard_formats([str(path)]))
            self.assertEqual(struct.unpack('<IiiII', formats[15][:20]), (20, 0, 0, 0, 1))
            self.assertEqual(formats[15][20:].decode('utf-16le'), str(path.resolve()) + '\0\0')
            self.assertEqual(struct.unpack_from('<ii', formats[8], 4), (3, 2))
            other = Path(directory) / 'notes.txt'
            other.write_text('hello')
            self.assertEqual([kind for kind, _ in clipboard_formats([str(other)])], [15])
            self.assertEqual([kind for kind, _ in clipboard_formats([str(path), str(other)])], [15])

    def test_endpoint_stages_all_files_without_filename_collisions(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(app.tempfile, 'mkdtemp', return_value=directory), patch.object(app, 'copy_files') as copy:
                response = app.app.test_client().post('/clipboard/files', data={
                    'file': [(io.BytesIO(b'first'), 'same.txt'), (io.BytesIO(b'second'), 'same.txt')]})
                self.assertEqual(response.status_code, 200)
                paths = copy.call_args.args[0]
                self.assertEqual([Path(p).read_bytes() for p in paths], [b'first', b'second'])
                self.assertEqual(response.json['filenames'], ['same.txt', 'same.txt'])

    def test_missing_file_and_clipboard_failure(self):
        client = app.app.test_client()
        self.assertEqual(client.post('/clipboard/files').status_code, 400)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(app.tempfile, 'mkdtemp', return_value=directory), patch.object(app, 'copy_files', side_effect=OSError('busy')):
                response = client.post('/clipboard/files', data={'file': (io.BytesIO(b'a'), 'a.txt')})
                self.assertEqual(response.status_code, 500)
                self.assertIn('busy', response.json['error'])

    def test_page_and_existing_text_clipboard(self):
        client = app.app.test_client()
        page = client.get('/').get_data(as_text=True)
        self.assertNotIn('Send a screenshot', page)
        self.assertIn('iOS Clipboard', page)
        self.assertIn('Receive a file from Windows', page)
        self.assertIn('Recents', page)
        with patch.object(app.pyperclip, 'copy') as copy:
            self.assertEqual(client.post('/clipboard', json={'text': 'hello'}).status_code, 200)
            copy.assert_called_once_with('hello')

    def test_get_clipboard_text_and_failure(self):
        client = app.app.test_client()
        with patch.object(app.pyperclip, 'paste', return_value='text from Windows'):
            response = client.get('/clipboard')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, {'text': 'text from Windows'})
        with patch.object(app.pyperclip, 'paste', side_effect=app.pyperclip.PyperclipException('unavailable')):
            response = client.get('/clipboard')
            self.assertEqual(response.status_code, 500)
            self.assertIn('unavailable', response.json['error'])

    def test_file_list_and_download(self):
        client = app.app.test_client()
        with tempfile.TemporaryDirectory() as directory:
            previous_folder = app.transfer_folder
            app.transfer_folder = directory
            try:
                Path(directory, 'second.txt').write_text('second')
                Path(directory, 'first.txt').write_text('first')
                response = client.get('/api/files')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    [item['name'] for item in response.json['files']],
                    ['first.txt', 'second.txt'],
                )
                first_file = response.json['files'][0]
                self.assertEqual(first_file['size'], 5)
                download = client.get(first_file['download_url'])
                try:
                    self.assertEqual(download.status_code, 200)
                    self.assertEqual(download.data, b'first')
                finally:
                    download.close()
            finally:
                app.transfer_folder = previous_folder

    def test_selected_iphone_files_can_be_listed_and_downloaded(self):
        client = app.app.test_client()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'from-pc.txt')
            path.write_text('send this to iPhone')
            file_id = 'selected-file-id'
            with patch.dict(app.OUTGOING_FILES, {file_id: path}, clear=True):
                response = client.get('/api/iphone-files')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.json['files']), 1)
                item = response.json['files'][0]
                self.assertEqual(item['id'], file_id)
                self.assertEqual(item['name'], path.name)
                self.assertEqual(item['size'], path.stat().st_size)
                self.assertTrue(item['download_url'].endswith('/iphone-download/' + file_id))

                download = client.get(item['download_url'])
                try:
                    self.assertEqual(download.status_code, 200)
                    self.assertEqual(download.data, b'send this to iPhone')
                    self.assertIn('attachment', download.headers['Content-Disposition'])
                finally:
                    download.close()

                missing = client.get('/iphone-download/not-selected')
                self.assertEqual(missing.status_code, 404)


if __name__ == '__main__':
    unittest.main()
