"""Kloud page blood flow videos: both BEMER videos embedded privacy-enhanced, each with Glen's label."""
import re
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "static" / "kloud.html"
LABEL = ("Blood flow before, during and after a session on a BEMER mat. "
         "BEMER is an earlier PEMF device from another company developed by the same researchers. "
         "This video was not filmed on a Kloud.")
IDS = ["7kuzFcF7e5w", "sg8hz8B87gk"]


def _section(html):
    m = re.search(r'<section[^>]*id="blood-flow-videos"[^>]*>.*?</section>', html, re.S)
    assert m, "blood-flow-videos section missing"
    return m


def _figures():
    html = PAGE.read_text(encoding="utf-8")
    assert len(re.findall(r"<figure\b[^>]*\bvideo\b", html)) == 2, "video figures outside the section"
    return html, re.findall(r"<figure\b[^>]*\bvideo\b.*?</figure>", _section(html).group(0), re.S)


def test_both_videos_embedded_with_label():
    _, figs = _figures()
    assert len(figs) == 2
    for vid, fig in zip(IDS, figs):
        assert f'src="https://www.youtube-nocookie.com/embed/{vid}"' in fig
        cap = re.search(r"<figcaption[^>]*>(.*?)</figcaption>", fig, re.S).group(1).strip()
        assert cap == LABEL


def test_no_tracking_youtube_embed():
    html, _ = _figures()
    assert "youtube.com/embed" not in html.replace("youtube-nocookie.com/embed", "")


def test_videos_sit_after_intro_before_models():
    html, _ = _figures()
    i_pemf = html.index("PEMF stands for Pulsed Electromagnetic Fields")
    sec = _section(html)
    i_models = html.index(">Two models<")
    assert i_pemf < sec.start() and sec.end() < i_models


def test_kloud_fibroblast_video_after_bemer_before_models():
    html = PAGE.read_text(encoding="utf-8")
    m = re.search(r'<section[^>]*id="kloud-video"[^>]*>.*?</section>', html, re.S)
    assert m
    sec = m.group(0)
    assert 'src="https://www.youtube-nocookie.com/embed/H3qdRdE1lq4"' in sec
    assert "BEMER" not in sec
    assert _section(html).end() < m.start() and m.end() < html.index(">Two models<")
