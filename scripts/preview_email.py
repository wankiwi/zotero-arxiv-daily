"""Render fictional examples locally. No network, library access or email delivery."""
from datetime import datetime, timezone
from pathlib import Path
from zotero_arxiv_daily.protocol import Paper
from zotero_arxiv_daily.construct_email import render_email, email_plain_text


def samples():
    return [
        Paper(source='journals', journal='Example Materials Journal',
              title='Learning molecular interactions across scales: a reproducible route to soft materials design',
              authors=['Ada Example', 'Lin Chen', 'Sam Rivera'], affiliations=['Example Institute of Materials'],
              abstract='Fictional preview. We investigate how molecular interactions shape the response of soft materials. A combined simulation and measurement workflow connects local structure to macroscopic transport.\nThe complete abstract remains readable without hidden text or an external service.',
              url='https://example.org/paper/1', pdf_url='https://example.org/paper/1.pdf', doi='10.0000/preview.1',
              score=7.54, published=datetime(2026,9,30,tzinfo=timezone.utc), tldr_status='not_generated'),
        Paper(source='arxiv', title='面向复杂催化界面的机器学习：跨尺度建模与可解释性 / Machine learning at catalytic interfaces',
              authors=[f'Researcher {i}' for i in range(1,12)], affiliations=['Fictional Chemistry Laboratory'],
              abstract='虚构预览：保留完整的多语言摘要，展示低相关性分数以及摘要服务不可用时的真实状态。界面结构、反应路径与预测误差在统一框架下进行比较。',
              url='https://example.org/paper/2', score=-0.15, tldr_status='fallback', scoring_basis='abstract'),
        Paper(source='biorxiv', title='A fictional protein dynamics study', authors=['Noor Example'],
              abstract='Original abstract for a fictional preview.', tldr='Fictional AI summary example; no AI service was called to create this preview.',
              tldr_status='generated', url='https://example.org/paper/3', score=6.28),
        Paper(source='researchsquare', title='A preview with missing metadata', authors=[], abstract='',
              url='https://example.org/paper/4', score=None, scoring_basis='title only', tldr_status='not_generated'),
    ]


if __name__ == '__main__':
    directory = Path('docs/email-preview')
    directory.mkdir(parents=True, exist_ok=True)
    for name, papers in [('email-preview', samples()), ('empty-preview', [])]:
        html = render_email(papers)
        (directory / f'{name}.html').write_text(html, encoding='utf-8')
        (directory / f'{name}.txt').write_text(email_plain_text(html), encoding='utf-8')
