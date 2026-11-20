"""Conversions for media, tables and text documents."""
import csv
from html import escape
from html.parser import HTMLParser
import json

AUDIO = {'WAV': '.wav', 'FLAC': '.flac', 'MP3': '.mp3', 'OGG': '.ogg', 'M4A': '.m4a'}
VIDEO = {'MKV': '.mkv', 'MP4': '.mp4'}
TABLE = {'CSV': '.csv', 'TSV': '.tsv', 'JSON': '.json', 'XLSX': '.xlsx'}
DOCUMENT = {'TXT': '.txt', 'HTML': '.html', 'DOCX': '.docx'}


def convert_media(source, output, fmt, check):
    import av

    # Limit input to local media container formats.
    demuxers = ('mov,matroska,webm,wav,flac,mp3,ogg,aac,aiff,asf,avi,mpeg,'
                'flv,mpegts')
    with source.open('rb') as file, av.open(file, options={'format_whitelist': demuxers}) as media:
        if fmt in VIDEO:
            inputs = [s for s in media.streams if s.type in ('audio', 'video')]
            if not any(s.type == 'video' for s in inputs):
                raise ValueError('В исходном файле нет видео')
            if fmt == 'MP4':
                supported = {'h264', 'hevc', 'av1', 'mpeg4', 'vp9', 'aac', 'mp3', 'alac', 'ac3', 'eac3'}
                if any(s.codec_context.name not in supported for s in inputs):
                    raise ValueError('В MP4 нельзя перенести эти дорожки. Попробуйте MKV.')
            with av.open(str(output), 'w', format='matroska' if fmt == 'MKV' else 'mp4') as result:
                streams = {s.index: result.add_stream_from_template(s) for s in inputs}
                wrote = False
                for packet in media.demux(inputs):
                    check()
                    if packet.size:
                        packet.stream = streams[packet.stream.index]
                        result.mux(packet)
                        wrote = True
                if not wrote:
                    raise ValueError('В контейнере нет видеоданных')
            return

        if not media.streams.audio:
            raise ValueError('В исходном файле нет звуковой дорожки')
        source_stream = media.streams.audio[0]
        codec, muxer, sample_format = {
            'WAV': ('pcm_s24le', 'wav', 's32'),
            'FLAC': ('flac', 'flac', 's32'),
            'MP3': ('libmp3lame', 'mp3', 'fltp'),
            'OGG': ('libopus', 'ogg', 'flt'),
            'M4A': ('aac', 'ipod', 'fltp'),
        }[fmt]
        with av.open(str(output), 'w', format=muxer) as result:
            rate = source_stream.codec_context.sample_rate or 48000
            if fmt == 'OGG' or fmt in ('MP3', 'M4A') and rate not in (32000, 44100, 48000):
                rate = 48000
            layout = source_stream.codec_context.layout
            channels = len(layout.channels) if layout else 0
            if channels == 1:
                layout = 'mono'
            elif channels <= 2 or fmt in ('MP3', 'OGG'):
                layout = 'stereo'
            stream = result.add_stream(codec, rate=rate)
            stream.layout = layout
            stream.format = sample_format
            if fmt == 'FLAC':
                stream.options = {'compression_level': '3'}
            if fmt in ('MP3', 'M4A', 'OGG'):
                stream.bit_rate = 160000 if fmt != 'OGG' else 128000
            resampler = av.AudioResampler(format=sample_format, layout=layout, rate=rate)
            samples = 0
            for frame in media.decode(source_stream):
                check()
                frame.pts = None
                for chunk in resampler.resample(frame):
                    for packet in stream.encode(chunk):
                        result.mux(packet)
                samples += frame.samples
            if not samples:
                raise ValueError('Звуковая дорожка пуста')
            for chunk in resampler.resample(None):
                for packet in stream.encode(chunk):
                    result.mux(packet)
            for packet in stream.encode(None):
                result.mux(packet)


def load_text(path):
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Текстовый файл больше 32 МиБ')
    data = path.read_bytes()
    return data.decode('utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')


def table_rows(source):
    suffix = source.suffix.lower()
    if suffix in ('.csv', '.tsv'):
        import io

        with io.StringIO(load_text(source), newline='') as stream:
            yield from csv.reader(stream, delimiter=',' if suffix == '.csv' else '\t', strict=True)
    elif suffix == '.xlsx':
        from openpyxl import load_workbook

        book = load_workbook(source, read_only=True, data_only=False)
        try:
            if len(book.worksheets) != 1:
                raise ValueError('Для конвертации XLSX нужен один лист')
            yield from book.worksheets[0].values
        finally:
            book.close()
    else:
        if suffix == '.toml':
            import tomllib

            records = tomllib.loads(load_text(source))
            # Accept a single TOML array of tables.
            if len(records) == 1:
                records = next(iter(records.values()))
        else:
            records = json.loads(load_text(source))
        if not isinstance(records, list):
            raise ValueError('Таблица должна быть массивом строк или объектов')
        if not records:
            return
        if isinstance(records[0], dict):
            columns = list(records[0])
            yield columns
            for record in records:
                if not isinstance(record, dict) or record.keys() != records[0].keys():
                    raise ValueError('У всех объектов должен быть одинаковый набор полей')
                yield [record[key] for key in columns]
        else:
            if not all(isinstance(row, list) for row in records):
                raise ValueError('Каждая строка таблицы должна быть массивом')
            yield from records


def scalar(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (dict, list)):
        raise ValueError('Вложенные объекты в ячейках не поддерживаются')
    return str(value)


def convert_table(source, output, fmt, check):
    from contextlib import closing

    with closing(table_rows(source)) as rows:
        if fmt == 'XLSX':
            from openpyxl import Workbook
            from openpyxl.cell import WriteOnlyCell

            book = Workbook()
            sheet = book.active
            for row in rows:
                check()
                cells = []
                for value in map(scalar, row):
                    if isinstance(value, str) and len(value) > 32767:
                        raise ValueError('Ячейка превышает 32767 символов')
                    cell = WriteOnlyCell(sheet, value=value)
                    if isinstance(value, str):
                        cell.data_type = 's'
                    cells.append(cell)
                sheet.append(cells)
            book.save(output)
            return

        with output.open('w', encoding='utf-8-sig' if fmt != 'JSON' else 'utf-8', newline='') as stream:
            if fmt == 'JSON':
                stream.write('[')
                separator = '\n'
                for row in rows:
                    check()
                    stream.write(separator)
                    json.dump(list(map(scalar, row)), stream, ensure_ascii=False, allow_nan=False)
                    separator = ',\n'
                stream.write('\n]\n')
            else:
                writer = csv.writer(stream, delimiter=',' if fmt == 'CSV' else '\t')
                for row in rows:
                    check()
                    writer.writerow(map(scalar, row))


class PlainHTML(HTMLParser):
    blocks = {'p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'section'}

    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = None

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'head'):
            self.hidden = tag
        elif not self.hidden and tag in self.blocks:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag == self.hidden:
            self.hidden = None
        elif not self.hidden and tag in self.blocks:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def document_text(source, check):
    suffix = source.suffix.lower()
    if suffix == '.pdf':
        from pypdf import PdfReader

        with source.open('rb') as stream:
            reader = PdfReader(stream)
            if reader.is_encrypted:
                raise ValueError('PDF защищён паролем')
            pages = []
            for page in reader.pages:
                check()
                pages.append(page.extract_text())
            text = '\n\n'.join(pages)
            if not text.strip():
                raise ValueError('В PDF нет текстового слоя')
            return text
    if suffix == '.docx':
        from docx import Document

        doc = Document(source)
        parts = []
        for block in doc.iter_inner_content():
            check()
            if hasattr(block, 'rows'):
                parts.extend('\t'.join(cell.text for cell in row.cells) for row in block.rows)
            else:
                parts.append(block.text)
        return '\n'.join(parts)
    text = load_text(source)
    if suffix in ('.html', '.htm'):
        parser = PlainHTML()
        parser.feed(text)
        parser.close()
        return ''.join(parser.parts).strip()
    return text


def convert_document(source, output, fmt, check):
    text = document_text(source, check)
    check()
    if fmt == 'DOCX':
        from docx import Document

        doc = Document()
        for line in text.splitlines():
            check()
            doc.add_paragraph(line)
        doc.save(output)
    else:
        if fmt == 'HTML':
            text = '<!doctype html>\n<meta charset="utf-8">\n<title>Документ</title>\n<pre>' + escape(text) + '</pre>\n'
        output.write_text(text, encoding='utf-8')
