"""Journal presets. Nature coverage is refreshed from its official site index."""
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
import re


@dataclass(frozen=True)
class Journal:
    id: str
    title: str
    issns: tuple[str, ...] = ()
    rss: str | None = None

    def __post_init__(self):
        for issn in self.issns:
            if not re.fullmatch(r'\d{4}-\d{3}[\dX]', issn):
                raise ValueError(f'Invalid ISSN for {self.title}: {issn}')
        if self.rss and urlsplit(self.rss).scheme != 'https':
            raise ValueError(f'Journal feeds must use HTTPS: {self.title}')


CORE = {
    'jacs': Journal('jacs', 'Journal of the American Chemical Society', ('0002-7863', '1520-5126'), 'https://pubs.acs.org/action/showFeed?type=etoc&feed=rss&jc=jacsat'),
    'jctc': Journal('jctc', 'Journal of Chemical Theory and Computation', ('1549-9618', '1549-9626'), 'https://pubs.acs.org/action/showFeed?type=etoc&feed=rss&jc=jctcce'),
    'prl': Journal('prl', 'Physical Review Letters', ('0031-9007', '1079-7114'), 'https://journals.aps.org/rss/recent/prl.xml'),
    'nature': Journal('nature', 'Nature', ('0028-0836', '1476-4687'), 'https://www.nature.com/nature.rss'),
    'science': Journal('science', 'Science', ('0036-8075', '1095-9203'), 'https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=science'),
    'science_advances': Journal('science_advances', 'Science Advances', ('2375-2548',), 'https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=advances'),
    'jcp': Journal('jcp', 'The Journal of Chemical Physics', ('0021-9606', '1089-7690'), None),
    'jpcl': Journal('jpcl', 'The Journal of Physical Chemistry Letters', ('1948-7185',), 'https://pubs.acs.org/action/showFeed?type=etoc&feed=rss&jc=jpclcd'),
}
# Offline bootstrap list; live discovery adds new Nature-branded journals.
_NATURE = {
    'nature': 'Nature', 'nataging': 'Nature Aging', 'natastron': 'Nature Astronomy',
    'natbiomedeng': 'Nature Biomedical Engineering', 'nbt': 'Nature Biotechnology',
    'natcardiovascres': 'Nature Cardiovascular Research', 'natcatal': 'Nature Catalysis',
    'ncb': 'Nature Cell Biology', 'nchembio': 'Nature Chemical Biology',
    'natchemeng': 'Nature Chemical Engineering', 'nchem': 'Nature Chemistry',
    'natcities': 'Nature Cities', 'nclimate': 'Nature Climate Change',
    'ncomms': 'Nature Communications', 'natcomputsci': 'Nature Computational Science',
    'natecolevol': 'Nature Ecology & Evolution', 'natelectron': 'Nature Electronics',
    'natenergy': 'Nature Energy', 'natfood': 'Nature Food', 'ng': 'Nature Genetics',
    'ngeo': 'Nature Geoscience', 'nathumbehav': 'Nature Human Behaviour',
    'ni': 'Nature Immunology', 'natmachintell': 'Nature Machine Intelligence',
    'nmat': 'Nature Materials', 'nm': 'Nature Medicine', 'natmentalhealth': 'Nature Mental Health',
    'natmetab': 'Nature Metabolism', 'nmeth': 'Nature Methods', 'natmicrobiol': 'Nature Microbiology',
    'nnano': 'Nature Nanotechnology', 'nn': 'Nature Neuroscience', 'nphoton': 'Nature Photonics',
    'nphys': 'Nature Physics', 'nplants': 'Nature Plants', 'nprot': 'Nature Protocols',
    'nsmb': 'Nature Structural & Molecular Biology', 'natsustain': 'Nature Sustainability',
    'natsynth': 'Nature Synthesis', 'natwater': 'Nature Water',
    'natrevbioeng': 'Nature Reviews Bioengineering', 'nrc': 'Nature Reviews Cancer',
    'nrcardio': 'Nature Reviews Cardiology', 'natrevchem': 'Nature Reviews Chemistry',
    'nrclinonc': 'Nature Reviews Clinical Oncology', 'nrdp': 'Nature Reviews Disease Primers',
    'nrd': 'Nature Reviews Drug Discovery', 'natrevearthenviron': 'Nature Reviews Earth & Environment',
    'natrevelectreng': 'Nature Reviews Electrical Engineering', 'nrendo': 'Nature Reviews Endocrinology',
    'nrgastro': 'Nature Reviews Gastroenterology & Hepatology', 'nrg': 'Nature Reviews Genetics',
    'nri': 'Nature Reviews Immunology', 'natrevmats': 'Nature Reviews Materials',
    'natrevmethodsprimers': 'Nature Reviews Methods Primers', 'nrmicro': 'Nature Reviews Microbiology',
    'nrm': 'Nature Reviews Molecular Cell Biology', 'nrneph': 'Nature Reviews Nephrology',
    'nrneurol': 'Nature Reviews Neurology', 'nrn': 'Nature Reviews Neuroscience',
    'natrevphys': 'Nature Reviews Physics', 'natrevpsychol': 'Nature Reviews Psychology',
    'nrrheum': 'Nature Reviews Rheumatology', 'nrurol': 'Nature Reviews Urology',
}
NATURE = {slug: CORE['nature'] if slug == 'nature' else Journal(slug, title, rss=f'https://www.nature.com/{slug}.rss') for slug, title in _NATURE.items()}


class NatureIndex(HTMLParser):
    def __init__(self):
        super().__init__()
        self.link = None
        self.text = []
        self.journals = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.link = dict(attrs).get('href')
            self.text = []

    def handle_data(self, data):
        if self.link:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag != 'a' or not self.link:
            return
        title = ' '.join(''.join(self.text).split())
        url = urlsplit(urljoin('https://www.nature.com/', self.link))
        slug = url.path.strip('/')
        not_journals = {'Nature Portfolio', 'Nature Research', 'Nature Index', 'Nature Careers', 'Nature Masterclasses', 'Nature Events', 'Nature Africa', 'Nature India', 'Nature Middle East', 'Nature Outlook', 'Nature Podcast', 'Nature Video'}
        if title not in not_journals and url.hostname == 'www.nature.com' and re.fullmatch(r'[a-z][a-z0-9-]*', slug) and (title == 'Nature' or title.startswith('Nature ')):
            self.journals[slug] = Journal(slug, title, rss=f'https://www.nature.com/{slug}.rss')
        self.link = None


def discover_nature(html: str) -> dict[str, Journal]:
    parser = NatureIndex()
    parser.feed(html)
    if len(parser.journals) < 20:
        raise ValueError('Nature site index did not contain a usable journal catalogue')
    return parser.journals


def selected_journals(config, discovered=None) -> list[Journal]:
    available = {**CORE, **NATURE, **(discovered or {})}
    selected = []
    for key in config.get('presets', []):
        if key == 'nature_family':
            selected.extend({**NATURE, **(discovered or {})}.values())
        elif key in available:
            selected.append(available[key])
        else:
            raise ValueError(f'Unknown journal preset: {key}')
    for item in config.get('custom', []):
        selected.append(Journal(item['id'], item['title'], tuple(item.get('issns', [])), item.get('rss')))
    return list({j.id: j for j in selected}.values())
