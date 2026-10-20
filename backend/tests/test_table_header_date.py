"""表头原始数字日期校验，防止视觉模型把十月日期误读为一月。"""

import json
import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw, ImageFont

from services.content import image_parser


@pytest.mark.parametrize('digits,wrong_date', [
    ('20261019', '2026-01-09'), ('20261016', '2026-01-06'),
])
@pytest.mark.parametrize('height', [400, 1466])
def test_header_ocr_corrects_model_date_before_saving(tmp_path, monkeypatch, digits, wrong_date, height):
    if not shutil.which('tesseract'):
        pytest.skip('需要本机 tesseract 验证真实表头 OCR')
    image = Image.new('RGB', (552, height), 'white')
    ImageDraw.Draw(image).text((30, 40), digits, fill='black', font=ImageFont.load_default(size=28))
    path = tmp_path / 'table.png'
    image.save(path)
    monkeypatch.setattr(image_parser, '_call_vision', lambda *a, **k: json.dumps({
        '更新日期': wrong_date, '数据': [{'指数名称': '中证红利', 'PE': 9.91}],
    }))

    result = image_parser.DDImageParser().parse(str(path))

    assert result['ok'] is True
    assert result['update_date'] == f'{digits[:4]}-{digits[4:6]}-{digits[6:]}'
    assert result['date_verification']['model_date'] == wrong_date
    assert result['date_verification']['raw_date'] == digits
    from db.valuations import save_dd_valuation, get_dd_valuation
    saved = get_dd_valuation(save_dd_valuation(result, str(path)))
    assert saved['update_date'] == result['update_date']
    assert saved['parsed_data']['update_date'] == result['update_date']


@pytest.mark.parametrize('text', ['20260230', '20261019\n20261016', '1232026101999', '563020 021707'])
def test_header_ocr_does_not_guess_invalid_or_ambiguous_dates(tmp_path, monkeypatch, text):
    from services.content.table_header_date import read_table_header_date
    path = tmp_path / 'table.png'
    Image.new('RGB', (552, 400), 'white').save(path)
    monkeypatch.setattr(shutil, 'which', lambda *a: '/usr/bin/tesseract')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, text.encode(), b''))
    assert read_table_header_date(str(path)) is None


@pytest.mark.parametrize('failure', ['missing', 'timeout', 'error'])
def test_unavailable_header_ocr_keeps_existing_parser_usable(tmp_path, monkeypatch, failure):
    from services.content.table_header_date import read_table_header_date
    path = tmp_path / 'table.png'
    Image.new('RGB', (552, 400), 'white').save(path)
    monkeypatch.setattr(shutil, 'which', lambda *a: None if failure == 'missing' else '/usr/bin/tesseract')
    def run(*a, **k):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired('tesseract', 10)
        return subprocess.CompletedProcess(a, 1, b'', b'OCR unavailable')
    monkeypatch.setattr(subprocess, 'run', run)
    assert read_table_header_date(str(path)) is None
