import tarfile
import re
import glob
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import parseaddr, formataddr
from loguru import logger
import datetime
import ssl
from contextlib import suppress
from pathlib import PurePosixPath
from omegaconf import DictConfig
def extract_tex_code_from_tar(file_path: str, paper_id: str) -> dict[str, str] | None:
    try:
        with tarfile.open(file_path) as archive:
            files = {}
            total_size = 0
            for member in archive.getmembers():
                if not member.isfile() or not member.name.endswith('.tex'):
                    continue
                total_size += member.size
                if total_size > 50 * 1024 * 1024:
                    raise ValueError('TeX archive exceeds 50 MiB text limit')
                with archive.extractfile(member) as source:
                    text = source.read().decode('utf-8', errors='ignore')
                text = re.sub(r'(?<!\\)%[^\n]*', '', text)
                text = re.sub(r'\\begin\{comment\}.*?\\end\{comment\}|\\iffalse.*?\\fi', '', text, flags=re.DOTALL)
                files[member.name.removeprefix('./')] = text
            if not files:
                return None
            bbl = [m.name.removeprefix('./').removesuffix('.bbl') + '.tex' for m in archive.getmembers() if m.name.endswith('.bbl')]
            main = bbl[0] if len(bbl) == 1 and bbl[0] in files else None
            if main is None:
                main = next((name for name, text in files.items() if r'\begin{document}' in text and not any(w in name for w in ('example', 'sample'))), None)
            if main is None and len(files) == 1:
                main = next(iter(files))
            def expand(name, visiting=frozenset()):
                if name in visiting:
                    logger.warning(f'Cyclic TeX include in {paper_id}: {name}')
                    return ''
                def include(match):
                    target = match.group(1)
                    if not target.endswith('.tex'):
                        target += '.tex'
                    relative = str(PurePosixPath(name).parent / target)
                    target = relative if relative in files else target
                    return expand(target, visiting | {name}) if target in files else ''
                return re.sub(r'\\(?:input|include)\{([^}]+)\}', include, files[name])
            files['all'] = expand(main) if main else None
            return files
    except tarfile.ReadError:
        logger.debug(f'No readable TeX archive for {paper_id}')
        return None

def extract_markdown_from_pdf(file_path:str) -> str:
    import pymupdf
    import pymupdf.layout
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.layout.activate()
    import pymupdf4llm
    return pymupdf4llm.to_markdown(file_path,use_ocr=False,header=False,footer=False,ignore_code=True)

def glob_match(path:str, pattern:str) -> bool:
    re_pattern = glob.translate(pattern,recursive=True)
    return re.match(re_pattern, path) is not None

def send_email(config:DictConfig, html:str):
    sender = config.email.sender
    receiver = config.email.receiver
    password = config.email.sender_password
    smtp_server = config.email.smtp_server
    smtp_port = config.email.smtp_port
    def _format_addr(s):
        name, addr = parseaddr(s)
        return formataddr((Header(name, 'utf-8').encode(), addr))

    msg = MIMEText(html, 'html', 'utf-8')
    msg['From'] = _format_addr('Github Action <%s>' % sender)
    msg['To'] = _format_addr('You <%s>' % receiver)
    today = datetime.datetime.now().strftime('%Y/%m/%d')
    msg['Subject'] = Header(f'Daily Papers {today}', 'utf-8').encode()

    context = ssl.create_default_context()
    # CSTNET documents 994 as implicit TLS, alongside standard SMTPS port 465.
    implicit_tls = int(smtp_port) in (465, 994)
    server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30, context=context) if implicit_tls else smtplib.SMTP(smtp_server, smtp_port, timeout=30)
    try:
        if not implicit_tls:
            server.starttls(context=context)
        server.login(sender, password)
        refused = server.sendmail(sender, [receiver], msg.as_string())
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    finally:
        try:
            server.quit()
        except Exception:
            with suppress(Exception):
                server.close()
