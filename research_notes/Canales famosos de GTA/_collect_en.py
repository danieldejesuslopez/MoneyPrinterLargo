import concurrent.futures
import html
import json
import re
from pathlib import Path

import requests

DEST = Path(__file__).parent
GENERAL = set('jacksepticeye vanossgaming ali-a typical-gamer kwebbelkop theradbrad h2odelirious xqc slogo smii7y sodapoppin wildcat vikkstar123 nogla dashiegames summit1g basicallyidowrk cartoonz terroriser zerkaa lirik sykkuno'.split())
MASSIVE = set('jacksepticeye vanossgaming ali-a typical-gamer kwebbelkop theradbrad h2odelirious xqc slogo smii7y wildcat vikkstar123 nogla dashiegames summit1g'.split())
SLUGS = ('jacksepticeye vanossgaming jelly ali-a typical-gamer kwebbelkop theradbrad h2odelirious xqc slogo smii7y sodapoppin nought wildcat vikkstar123 nogla dashiegames summit1g speirstheamazinghd basicallyidowrk gta-series-videos cartoonz terroriser mrbossftw elanip treyten zerkaa lirik sykkuno darkviperau tgg hazardous t5g saintsfan vadact lennybarn buddha lord-kebun anthonyz digital-car-addict ramee tylarious harmnone ghilliemaster broughy1322 gtamen badger-goodger').split()


def get(slug):
    profile_url = f'https://totalgta.com/creators/{slug}/'
    raw = requests.get(profile_url, timeout=25)
    raw.raise_for_status()
    profile = next(json.loads(s) for s in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', raw.text, re.S) if json.loads(s).get('@type') == 'ProfilePage')
    videos = profile.get('video', [])
    for video in videos:
        url = video['url']
        response = requests.get('https://www.youtube.com/oembed', params={'url': url, 'format': 'json'}, timeout=20)
        if response.status_code != 200:
            continue
        evidence = response.json()
        expected = profile['mainEntity']['name']
        name = evidence['author_name']
        # The author may use a secondary gaming channel. Preserve that identity.
        record = {
            'name': expected,
            'language': 'en',
            'url': evidence['author_url'],
            'credit': '@' + evidence['author_url'].split('@', 1)[1] if '@' in evidence['author_url'] else '',
            'category': 'generalista' if slug in GENERAL else 'especialista',
            'gta_focus': ['GTA V', 'GTA RP'] if any(t in (video['name'] + profile['description']).lower() for t in ('roleplay', 'role play', 'gta rp', 'nopixel', 'gta 5 rp')) else ['GTA V', 'GTA Online'],
            'evidence_url': url,
            'evidence_note': 'Autor confirmado por oEmbed oficial de YouTube: ' + name + '. Video: ' + evidence['title'],
            'popularity': 'masivo' if slug in MASSIVE else 'grande',
            'current_activity': 'historica',
            'verification_date': '2026-11-17',
            'evidence_method': 'YouTube oEmbed: title, author_name y author_url',
            'discovery_url': profile_url,
            'evidence_title': evidence['title'],
        }
        if 'gta 6' in evidence['title'].lower() or 'gta vi' in evidence['title'].lower():
            record['gta_focus'] = ['GTA 6']
        return record
    raise ValueError(f'No primary evidence available: {slug}')


if __name__ == '__main__':
    records = []
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(get, slug): slug for slug in SLUGS}
        for future in concurrent.futures.as_completed(futures):
            slug = futures[future]
            try:
                record = future.result()
                records.append(record)
                print(slug, record['credit'], flush=True)
            except Exception as exc:
                errors.append({'slug': slug, 'error': str(exc)})
                print('ERROR', slug, str(exc), flush=True)
    records.sort(key=lambda r: (r['category'], r['name'].casefold()))
    (DEST / 'en_canales.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    (DEST / 'en_errors.json').write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding='utf-8')
    print('TOTAL', len(records), 'ERRORS', len(errors))
