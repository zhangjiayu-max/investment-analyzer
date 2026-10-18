"""长估值表按尺寸覆盖分块；仅模拟视觉响应，不访问网络。"""

import base64
import io
import json

import pytest
from PIL import Image, ImageDraw

from services.content import image_parser


def table_image(tmp_path, width=827, height=4130):
    image = Image.new('RGB', (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        draw.line((0, y, width, y), fill=(y // 256, y % 256, 180))
    path = tmp_path / 'table.png'
    image.save(path)
    return path


def response(rows, **metadata):
    return json.dumps({'数据': rows, **metadata}, ensure_ascii=False)


def row(name, code=None, pe=12.5):
    return {'指数名称': name, '指数代码': code, 'PE': pe, 'PB': 1.2}


@pytest.mark.parametrize('parser_class', [image_parser.DDImageParser, image_parser.LiuyiImageParser])
def test_long_table_is_split_before_accepting_partial_full_image_success(tmp_path, monkeypatch, parser_class):
    path = table_image(tmp_path)
    expected_y = list(range(350, 3950, 40))
    calls = []

    def vision(prompt, encoded, mime, **kwargs):
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as crop:
            red, green, _ = crop.getpixel((0, 0))
            top = red * 256 + green
            calls.append((top, crop.height, prompt))
            ys = expected_y[:15] if crop.height > 1200 else [y for y in expected_y if top + 10 <= y < top + crop.height - 10]
        return response([row(f'指数{y}') for y in ys], 更新日期='2026-08-06', 市场温度=58.7)

    monkeypatch.setattr(image_parser, '_call_vision', vision)
    result = parser_class().parse(str(path))
    assert result['ok'] is True
    assert [item['index_name'] for item in result['data']] == [f'指数{y}' for y in expected_y]
    assert all(height <= 1200 for _, height, _ in calls)
    assert calls[0][0] == 0
    assert calls[-1][0] + calls[-1][1] == 4130
    assert all(top < old_top + old_height for (old_top, old_height, _), (top, _, _) in zip(calls, calls[1:]))
    assert result['update_date'] == '2026-08-06'
    assert result['market_temperature'] == 58.7
    full_prompt = image_parser.DD_PARSE_PROMPT if parser_class is image_parser.DDImageParser else image_parser.LIUYI_PARSE_PROMPT
    crop_prompt = image_parser.DD_PARSE_PROMPT_CROP if parser_class is image_parser.DDImageParser else image_parser.LIUYI_PARSE_PROMPT_CROP
    assert calls[0][2] == full_prompt
    assert all(prompt == crop_prompt for _, _, prompt in calls[1:])


@pytest.mark.parametrize('failure', ['invalid', 'exception'])
@pytest.mark.parametrize('parser_class', [image_parser.DDImageParser, image_parser.LiuyiImageParser])
def test_any_failed_segment_reports_partial_failure_and_keeps_reviewable_rows(tmp_path, monkeypatch, failure, parser_class):
    path = table_image(tmp_path)
    calls = []

    def vision(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            if failure == 'exception':
                raise RuntimeError('segment unavailable')
            return response([])
        return response([row(f'指数{len(calls)}')], 更新日期='2026-08-06', 市场温度=25)

    monkeypatch.setattr(image_parser, '_call_vision', vision)
    result = parser_class().parse(str(path))
    assert result['ok'] is False
    assert result['count'] > 0
    assert result['partial'] is True
    assert result['failed_segments'][0]['segment'] == 2
    assert result['update_date'] == '2026-08-06'
    assert result['market_temperature'] == 25
    assert len(calls) >= 3


@pytest.mark.parametrize('parser_class', [image_parser.DDImageParser, image_parser.LiuyiImageParser])
def test_overlap_merge_preserves_same_named_distinct_indexes(tmp_path, monkeypatch, parser_class):
    path = table_image(tmp_path, height=2000)
    replies = iter([
        response([row('市场指数', 'A'), row('边界指数', 'B')]),
        response([row('边界指数', 'B'), row('市场指数', 'C')]),
    ])
    monkeypatch.setattr(image_parser, '_call_vision', lambda *args, **kwargs: next(replies))
    result = parser_class().parse(str(path))
    assert result['ok'] is True
    assert [(x['index_name'], x['index_code']) for x in result['data']] == [('市场指数', 'A'), ('边界指数', 'B'), ('市场指数', 'C')]


def test_short_table_stays_one_call(tmp_path, monkeypatch):
    path = table_image(tmp_path, height=500)
    calls = []
    def vision(*args, **kwargs):
        calls.append(1)
        return response([row('沪深300')], 更新日期='2026-08-06', 市场温度=25)
    monkeypatch.setattr(image_parser, '_call_vision', vision)
    assert image_parser.LiuyiImageParser().parse(str(path))['ok'] is True
    assert len(calls) == 1


def test_overlap_index_code_takes_priority_over_small_ocr_differences(tmp_path, monkeypatch):
    path = table_image(tmp_path, height=2000)
    replies = iter([
        response([row('沪深300', '000300', pe=12.5)]),
        response([row('沪深300', '000300', pe=12.51), row('其他指数', '000999')]),
    ])
    monkeypatch.setattr(image_parser, '_call_vision', lambda *args, **kwargs: next(replies))
    result = image_parser.LiuyiImageParser().parse(str(path))
    assert result['ok'] is True
    assert result['count'] == 2


def test_real_dd_overlap_color_difference_does_not_duplicate_numeric_row(tmp_path, monkeypatch):
    path = table_image(tmp_path, height=2000)
    first = {**row('基本面50', pe=11.46), '背景颜色': '黄色', '估值状态': '适中', '股息率': 4.22, 'ROE': 7.85}
    second = {**first, '背景颜色': '橙色', '估值状态': None}
    replies = iter([response([first]), response([second, row('下一指数')])])
    monkeypatch.setattr(image_parser, '_call_vision', lambda *args, **kwargs: next(replies))
    result = image_parser.DDImageParser().parse(str(path))
    assert result['ok'] is True
    assert result['count'] == 2
    assert result['data'][0]['valuation_status'] == '适中'


def test_transparent_webp_crops_are_encoded_as_rgb(tmp_path, monkeypatch):
    path = tmp_path / 'table.webp'
    Image.new('RGBA', (827, 2000), (255, 255, 255, 128)).save(path)
    calls = []
    def vision(prompt, encoded, mime, **kwargs):
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as crop:
            assert crop.mode == 'RGB'
        calls.append(1)
        return response([row(f'指数{len(calls)}')])
    monkeypatch.setattr(image_parser, '_call_vision', vision)
    result = image_parser.DDImageParser().parse(str(path))
    assert result['ok'] is True
    assert len(calls) == 2
