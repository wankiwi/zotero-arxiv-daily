"""Create a credential-free preview with synthetic papers; never send email."""
from pathlib import Path
from zotero_arxiv_daily.protocol import Paper
from zotero_arxiv_daily.construct_email import render_email, email_plain_text

def main():
    destination = Path('docs/previews')
    destination.mkdir(parents=True, exist_ok=True)
    papers = []
    for group, count in [('journals', 25), ('preprints', 15), ('random', 5)]:
        for index in range(count):
            papers.append(Paper(source='journals' if group == 'journals' else 'openreview',
                title=f'Synthetic {group} paper {index+1}: Molecular dynamics and scientific machine learning across complex materials',
                authors=['Example Author'], abstract='' if index == 1 else 'Synthetic abstract for layout testing only. No research findings are represented. ' * (12 if index == 0 else 1),
                tldr='该合成示例仅用于检验中文一句话摘要和邮件排版。' if index == 0 else None,
                tldr_status='generated' if index == 0 else 'not_generated',
                affiliations=['Example Institute of Molecular Science; ' * 18] if index == 0 else ['Example University'],
                score=8-index/10, url='https://example.org/paper', recommendation_group=group))
    showcase = [papers[0], papers[26], papers[42]]
    showcase[-1].tldr_status, showcase[-1].tldr_error = 'fallback', 'request_failed'
    for name, items in [('daily', papers), ('empty', []), ('shortage', papers[:2]), ('showcase', showcase)]:
        html=render_email(items)
        (destination/f'{name}.html').write_text(html,encoding='utf-8')
        (destination/f'{name}.txt').write_text(email_plain_text(html),encoding='utf-8')
    print('Synthetic email previews written to docs/previews')

if __name__=='__main__':main()
