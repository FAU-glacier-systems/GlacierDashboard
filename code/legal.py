"""Legal pages (Impressum, Datenschutz, Barrierefreiheit) in German and English, served as plain HTML next to the
Dash app. The German pages are the binding ones; the English pages are translations.

Content follows the FAU central pages (fau.de/impressum, /datenschutz, /barrierefreiheit).
"""
from html import escape

# Person responsible for the content of this site (§ 18 Abs. 2 MStV) and contact for visitors.
CONTACT_NAME = "Oskar Herrmann"
CONTACT_EMAIL = "oskar.herrmann@fau.de"
CONTACT_INSTITUTE = {"de": "Institut für Geographie", "en": "Institute of Geography"}
CONTACT_ADDRESS = "Wetterkreuz 15, 91058 Erlangen"

STATEMENT_DATE = {"de": "07.10.2026", "en": "7 October 2026"}

# slug per page and language; the dashboard's footer links to the pages in its own language
PAGES = {
    "de": {"impressum": "Impressum", "datenschutz": "Datenschutz", "barrierefreiheit": "Barrierefreiheit"},
    "en": {"imprint": "Imprint", "privacy": "Privacy", "accessibility": "Accessibility"},
}
OTHER = {"impressum": "imprint", "datenschutz": "privacy", "barrierefreiheit": "accessibility"}
OTHER.update({v: k for k, v in OTHER.items()})

# where the data sources are listed (the © link on the map)
SOURCES_URL = {"en": "/imprint#sources", "de": "/impressum#quellen"}
SOURCES_TITLE = {
    "en": ("Sources: glacier outlines RGI 7.0 (CC BY 4.0); terrain: Terrain Tiles (Mapzen, AWS) "
           "incl. EU-DEM (Copernicus) and DGM Austria (CC BY 4.0); map: MapLibre"),
    "de": ("Quellen: Gletscherumrisse RGI 7.0 (CC BY 4.0); Gelände: Terrain Tiles (Mapzen, AWS) "
           "mit EU-DEM (Copernicus) und DGM Österreich (CC BY 4.0); Karte: MapLibre"),
}
SOURCES_LABEL = {"en": "Data sources and licences", "de": "Datenquellen und Lizenzen"}

# the dashboard per language (layout.py), and the switch to the other language
HOME = {"en": "/", "de": "/de"}


def _contact(lang):
    return f"""<p>{escape(CONTACT_NAME)}<br>Friedrich-Alexander-Universität Erlangen-Nürnberg<br>
{escape(CONTACT_INSTITUTE[lang])}<br>{escape(CONTACT_ADDRESS)}<br>
E-Mail: <a href="mailto:{escape(CONTACT_EMAIL)}">{escape(CONTACT_EMAIL)}</a></p>"""


_FAU = """<p>Friedrich-Alexander-Universität Erlangen-Nürnberg (FAU)<br>Freyeslebenstraße 1<br>91058 Erlangen<br>
E-Mail: <a href="mailto:poststelle@fau.de">poststelle@fau.de</a></p>"""

_STMWK = """<p>Bayerisches Staatsministerium für Wissenschaft und Kunst<br>Salvatorstraße 2<br>80327 München<br>
E-Mail: <a href="mailto:poststelle@stmwk.bayern.de">poststelle@stmwk.bayern.de</a></p>"""

_DPO = """<p>Klaus Hoogestraat<br>c/o ITM Gesellschaft für IT-Management mbH<br>Bürgerstraße 81<br>01127 Dresden<br>
Telefon: +49 9131 85-25860<br>
E-Mail: <a href="mailto:datenschutzbeauftragter@fau.de">datenschutzbeauftragter@fau.de</a></p>"""

_BAYLDA = """<p>Der Bayerische Landesbeauftragte für den Datenschutz<br>Postfach 22 12 19<br>80502 München<br>
E-Mail: <a href="mailto:poststelle@datenschutz-bayern.de">poststelle@datenschutz-bayern.de</a><br>
<a href="https://www.datenschutz-bayern.de/">www.datenschutz-bayern.de</a></p>"""

_BITV = """<p>Landesamt für Digitalisierung, Breitband und Vermessung<br>IT-Dienstleistungszentrum des Freistaats Bayern<br>
Durchsetzungs- und Überwachungsstelle für barrierefreie Informationstechnik<br>
St.-Martin-Straße 47<br>81541 München<br>
E-Mail: <a href="mailto:bitv@bayern.de">bitv@bayern.de</a><br>
<a href="https://www.ldbv.bayern.de/digitalisierung/bitv/">www.ldbv.bayern.de/digitalisierung/bitv/</a></p>"""

_CC_BY = '<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>'
_RGI = ('RGI 7.0 Consortium (2023): Randolph Glacier Inventory – A Dataset of Global Glacier Outlines, Version 7.0. '
        'NSIDC, <a href="https://doi.org/10.5067/f6jmovy5navz">doi:10.5067/f6jmovy5navz</a>')
_TERRAIN = '<a href="https://registry.opendata.aws/terrain-tiles/">Terrain Tiles</a> (Mapzen, AWS Open Data)'
_TERRAIN_CREDIT = ("Produced using Copernicus data and information funded by the European Union – EU-DEM layers; "
                   "© offene Daten Österreichs – Digitales Geländemodell (DGM) Österreich")
_TERRAIN_ALL = '<a href="https://github.com/tilezen/joerd/blob/master/docs/attribution.md">'
_FRAGILE = '<a href="https://cordis.europa.eu/project/id/948290">FRAGILE</a>'

BODIES = {
    # ---------------------------------------------------------------- German
    "impressum": f"""
<h2>Anbieter</h2>
{_FAU}
<p>Die Friedrich-Alexander-Universität Erlangen-Nürnberg ist gemäß Art. 4 Abs. 1 des Bayerischen
Hochschulinnovationsgesetzes (BayHIG) eine staatliche Einrichtung und zugleich eine Körperschaft des
öffentlichen Rechts. Sie wird gesetzlich vertreten durch den Präsidenten, Prof. Dr. Joachim Hornegger.</p>

<h2>Zuständige Aufsichtsbehörde</h2>
{_STMWK}

<h2>Umsatzsteuer-Identifikationsnummer</h2>
<p>DE 132507686</p>

<h2>Inhaltlich verantwortlich (§ 18 Abs. 2 MStV) und Kontakt</h2>
{_contact("de")}

<h2 id="quellen">Daten und Quellen</h2>
<p>Gletscherumrisse: {_RGI} ({_CC_BY}).</p>
<p>Geländemodell: {_TERRAIN} aus SRTM, GMTED2010, ETOPO1 und EU-DEM, ergänzt um das Gletscherbett aus den
Modellrechnungen. Enthält: {_TERRAIN_CREDIT} ({_CC_BY}). {_TERRAIN_ALL}Vollständige Quellenangaben</a>.</p>
<p>Kartenbibliothek: <a href="https://maplibre.org">MapLibre GL JS</a> (BSD-3-Clause).</p>

<h2>Förderung</h2>
<p>Dieses Projekt wurde vom Europäischen Forschungsrat (ERC) im Rahmen des Forschungs- und Innovationsprogramms
Horizont 2020 der Europäischen Union gefördert (Finanzhilfevereinbarung Nr. 948290, {_FRAGILE}). Die Inhalte
geben ausschließlich die Sicht der Autorinnen und Autoren wieder; die Exekutivagentur des Europäischen
Forschungsrats (ERCEA) ist nicht für die Verwendung der darin enthaltenen Informationen verantwortlich.</p>

<h2>Haftung für Inhalte und Links</h2>
<p>Die dargestellten Gletscherentwicklungen sind Modellprojektionen und mit Unsicherheiten behaftet. Für
die Inhalte externer Seiten, auf die verwiesen wird, sind ausschließlich deren Betreiber verantwortlich.</p>
""",

    "datenschutz": f"""
<h2>Verantwortlicher</h2>
{_FAU}
<p>Die FAU wird gesetzlich vertreten durch den Präsidenten, Prof. Dr. Joachim Hornegger.
Ansprechpartner für diese Website:</p>
{_contact("de")}

<h2>Datenschutzbeauftragter</h2>
{_DPO}

<h2>Hosting</h2>
<p>Diese Website wird auf Servern in der Cloud-Infrastruktur der FAU betrieben. Externe Hosting-Dienstleister
werden nicht eingesetzt.</p>

<h2>Server-Logdateien</h2>
<p>Beim Aufruf einer Seite dieser Website speichert der Webserver: IP-Adresse, Datum und Uhrzeit, aufgerufene
Adresse (einschließlich der gewählten Ansicht), HTTP-Statuscode, übertragene Datenmenge, Referrer-URL und
Browserkennung (User-Agent). Die Abrufe der Karten- und Modelldaten, die beim Bedienen der Karte entstehen,
werden nicht protokolliert. Die Daten dienen dem sicheren Betrieb und der Fehleranalyse und werden nicht
mit anderen Daten zusammengeführt.</p>
<p>Rechtsgrundlage ist Art. 6 Abs. 1 UAbs. 1 lit. e DSGVO in Verbindung mit Art. 4 Abs. 1 BayDSG.
Die Logdateien werden nach 14 Tagen automatisch gelöscht.</p>

<h2>Kartendienste</h2>
<p>Das Geländemodell (Terrain Tiles, bereitgestellt über Amazon Web Services) ruft unser eigener Server ab
und speichert es zwischen; die Kartenbibliothek MapLibre liefern wir selbst aus. Ihr Browser lädt alle Inhalte
dieser Website von unserem Server. Dabei werden keine Daten über Sie an Dritte übermittelt.</p>

<h2>Lokaler Speicher, Cookies</h2>
<p>Diese Website setzt keine Cookies und verwendet keine Analyse- oder Tracking-Dienste. Im lokalen Speicher
Ihres Browsers werden zwei Einstellungen abgelegt: die gewählte Darstellung (hell/dunkel) und, ob Sie den
Hinweis beim ersten Besuch geschlossen haben. Sie werden nicht an uns übertragen und dienen nur dazu, die
Website so anzuzeigen, wie Sie es gewählt haben (§ 25 Abs. 2 Nr. 2 TDDDG). Sie können sie jederzeit in Ihren
Browsereinstellungen löschen.</p>

<h2>Ihre Rechte</h2>
<p>Sie haben das Recht auf Auskunft (Art. 15 DSGVO), Berichtigung (Art. 16), Löschung (Art. 17),
Einschränkung der Verarbeitung (Art. 18) und Widerspruch (Art. 21). Wenden Sie sich dazu an den
Verantwortlichen oder den Datenschutzbeauftragten.</p>
<p>Sie haben außerdem das Recht, sich bei der Aufsichtsbehörde zu beschweren:</p>
{_BAYLDA}
""",

    "barrierefreiheit": f"""
<p>Die Friedrich-Alexander-Universität Erlangen-Nürnberg ist bemüht, ihre Websites im Einklang mit
Art. 13 BayBGG, Art. 1 BayBITV und der Richtlinie (EU) 2016/2102 barrierefrei zugänglich zu machen.
Diese Erklärung gilt für <a href="/">www.glacier-evolution.nat.fau.de</a>.</p>

<h2>Stand der Vereinbarkeit</h2>
<p>Diese Website ist wegen der folgenden Unvereinbarkeiten nur teilweise barrierefrei.</p>

<h2>Nicht barrierefreie Inhalte</h2>
<ul>
<li>Die 3D-Karte der Gletscher ist eine interaktive Grafik. Sie wird von Screenreadern nicht als Text
  ausgegeben. Die Kamera lässt sich mit der Tastatur bewegen, Gletscher lassen sich aber nur mit der Maus auf
  der Karte anklicken; Werte an einer Stelle der Karte werden nur beim Überfahren mit der Maus angezeigt.</li>
<li>Werte werden auf der Karte nur über Farben (mit Farbskala) dargestellt.</li>
<li>Inhalte in Leichter Sprache und Gebärdensprache werden nicht angeboten.</li>
</ul>
<p>Mit der Tastatur bedienbar sind: die Suche nach Gletschern (Pfeiltasten und Eingabetaste), die Auswahl der
dargestellten Größe und des Szenarios, der Jahresregler, das Abspielen der Jahre und die Umschaltung hell/dunkel.
Die Fläche der Auswahl im gewählten Jahr steht als Text neben dem Szenario.</p>
<p>Die Darstellung dreidimensionaler Modelldaten lässt sich nicht gleichwertig in Textform wiedergeben.
Die zugrunde liegenden Daten stellen wir auf Anfrage in tabellarischer Form zur Verfügung.</p>

<h2>Erstellung dieser Erklärung</h2>
<p>Diese Erklärung wurde am {STATEMENT_DATE["de"]} erstellt bzw. zuletzt überprüft und beruht auf einer
Selbstbewertung.</p>

<h2>Feedback und Kontakt</h2>
<p>Barrieren auf dieser Website oder Anfragen nach barrierefreien Inhalten können Sie uns melden:</p>
{_contact("de")}

<h2>Durchsetzungsverfahren</h2>
<p>Erhalten Sie innerhalb von sechs Wochen keine zufriedenstellende Antwort, können Sie sich an die
Durchsetzungsstelle wenden:</p>
{_BITV}
""",

    # ---------------------------------------------------------------- English
    "imprint": f"""
<p class="legal-note">This is a translation. The <a href="/impressum">German version</a> is legally binding.</p>

<h2>Provider</h2>
{_FAU}
<p>Friedrich-Alexander-Universität Erlangen-Nürnberg is a state institution and a corporation under public
law (Art. 4 (1) of the Bavarian Higher Education Innovation Act, BayHIG). It is legally represented by its
President, Prof. Dr. Joachim Hornegger.</p>

<h2>Supervisory authority</h2>
{_STMWK}

<h2>VAT identification number</h2>
<p>DE 132507686</p>

<h2>Responsible for the content (§ 18 (2) MStV) and contact</h2>
{_contact("en")}

<h2 id="sources">Data and sources</h2>
<p>Glacier outlines: {_RGI} ({_CC_BY}).</p>
<p>Terrain: {_TERRAIN} from SRTM, GMTED2010, ETOPO1 and EU-DEM, with the glacier bed from the model runs
merged in. Contains: {_TERRAIN_CREDIT} ({_CC_BY}). {_TERRAIN_ALL}Full attribution</a>.</p>
<p>Map library: <a href="https://maplibre.org">MapLibre GL JS</a> (BSD-3-Clause).</p>

<h2>Funding</h2>
<p>This project has received funding from the European Research Council (ERC) under the European Union's
Horizon 2020 research and innovation programme (grant agreement No 948290, {_FRAGILE}). The content reflects
only the authors' view; the European Research Council Executive Agency (ERCEA) is not responsible for any use
that may be made of the information it contains.</p>

<h2>Liability for content and links</h2>
<p>The glacier changes shown are model projections and are subject to uncertainty. The operators of
external sites linked from here are solely responsible for their content.</p>
""",

    "privacy": f"""
<p class="legal-note">This is a translation. The <a href="/datenschutz">German version</a> is legally binding.</p>

<h2>Controller</h2>
{_FAU}
<p>FAU is legally represented by its President, Prof. Dr. Joachim Hornegger. Contact for this website:</p>
{_contact("en")}

<h2>Data protection officer</h2>
{_DPO}

<h2>Hosting</h2>
<p>This website runs on servers in FAU's own cloud infrastructure. No external hosting providers are used.</p>

<h2>Server log files</h2>
<p>When you open a page of this website, the web server stores: IP address, date and time, the address
requested (including the chosen view), HTTP status code, amount of data sent, referrer URL and browser
identification (user agent). The map and model data requested while you use the map are not logged. The data
serve the secure operation of the site and the analysis of errors and are not combined with other data.</p>
<p>The legal basis is Art. 6 (1) subpara. 1 (e) GDPR in conjunction with Art. 4 (1) BayDSG.
The log files are deleted automatically after 14 days.</p>

<h2>Map services</h2>
<p>The terrain (Terrain Tiles, provided via Amazon Web Services) is fetched and cached by our own server; we
serve the map library MapLibre ourselves. Your browser loads all content of this website from our server.
No data about you are passed on to third parties.</p>

<h2>Local storage, cookies</h2>
<p>This website sets no cookies and uses no analytics or tracking services. Two settings are kept in your
browser's local storage: the chosen appearance (light/dark) and whether you closed the hint shown on your
first visit. They are not sent to us and only serve to show the website the way you chose (§ 25 (2) no. 2
TDDDG). You can delete them at any time in your browser settings.</p>

<h2>Your rights</h2>
<p>You have the right of access (Art. 15 GDPR), rectification (Art. 16), erasure (Art. 17), restriction of
processing (Art. 18) and to object (Art. 21). Please contact the controller or the data protection officer.</p>
<p>You also have the right to lodge a complaint with the supervisory authority:</p>
{_BAYLDA}
""",

    "accessibility": f"""
<p class="legal-note">This is a translation. The <a href="/barrierefreiheit">German version</a> is legally
binding.</p>
<p>Friedrich-Alexander-Universität Erlangen-Nürnberg strives to make its websites accessible in accordance
with Art. 13 BayBGG, Art. 1 BayBITV and Directive (EU) 2016/2102. This statement applies to
<a href="/">www.glacier-evolution.nat.fau.de</a>.</p>

<h2>Compliance status</h2>
<p>This website is partially accessible because of the following non-compliances.</p>

<h2>Content that is not accessible</h2>
<ul>
<li>The 3D map of the glaciers is an interactive graphic. Screen readers do not read it out as text. The camera
  can be moved with the keyboard, but glaciers can only be clicked on the map with a mouse, and values at a
  point of the map are only shown when hovering with the mouse.</li>
<li>Values are shown on the map by colour only (with a colour scale).</li>
<li>Content in Easy Language and sign language is not offered.</li>
</ul>
<p>Usable with the keyboard: the glacier search (arrow keys and Enter), the choice of the property shown and
of the scenario, the year slider, playing the years and the light/dark switch. The area of the selection in
the chosen year is shown as text next to the scenario.</p>
<p>Three-dimensional model data cannot be rendered equivalently as text. We provide the underlying data in
tabular form on request.</p>

<h2>Preparation of this statement</h2>
<p>This statement was prepared or last reviewed on {STATEMENT_DATE["en"]} and is based on a
self-assessment.</p>

<h2>Feedback and contact</h2>
<p>Please report barriers on this website or ask for accessible content:</p>
{_contact("en")}

<h2>Enforcement procedure</h2>
<p>If you do not receive a satisfactory answer within six weeks, you can contact the enforcement body:</p>
{_BITV}
""",
}

TEMPLATE = """<!DOCTYPE html>
<html lang="{lang}" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Glacier Dashboard</title>
<script>try{{var t=JSON.parse(localStorage.getItem("theme"));if(t==="light"||t==="dark")document.documentElement.dataset.theme=t;}}catch(e){{}}</script>
<link rel="icon" type="image/x-icon" href="/assets/favicon.ico">
<link rel="stylesheet" href="{css}">
</head>
<body class="legal-page">
<main class="legal">
<p class="legal-top"><a href="{home}">{back}</a><a href="/{other}" lang="{other_lang}" hreflang="{other_lang}">{other_label}</a></p>
<h1>{title}</h1>
{body}
<nav class="legal-links">{nav}</nav>
</main>
</body>
</html>"""

BACK = {"de": "← Zurück zum Dashboard", "en": "← Back to the dashboard"}
LANG_LABEL = {"de": "Deutsch", "en": "English"}


def legal_links(lang):
    """Footer links of the dashboard as Dash components: the legal pages in its language, then the dashboard in
    the other language (map3d.js keeps the current view in that link)."""
    from dash import html
    other = "en" if lang == "de" else "de"
    return html.Nav(className="legal-links", children=[
        *[html.A(title, href=f"/{slug}") for slug, title in PAGES[lang].items()],
        html.A(LANG_LABEL[other], href=HOME[other], lang=other, hrefLang=other, className="lang-switch"),
    ])


def sources_link(lang):
    """The small © on the map, linking to the data sources."""
    from dash import html
    return html.A("©", href=SOURCES_URL[lang], className="sources-link", title=SOURCES_TITLE[lang],
                  **{"aria-label": SOURCES_LABEL[lang]})


def register(server, css_url):
    for lang, pages in PAGES.items():
        other_lang = "en" if lang == "de" else "de"
        nav = "".join(f'<a href="/{slug}">{title}</a>' for slug, title in pages.items())
        for slug, title in pages.items():
            page = TEMPLATE.format(lang=lang, title=title, css=css_url, body=BODIES[slug], nav=nav, back=BACK[lang],
                                   home=HOME[lang],
                                   other=OTHER[slug], other_lang=other_lang, other_label=LANG_LABEL[other_lang])
            server.add_url_rule(f"/{slug}", endpoint=f"legal_{slug}", view_func=lambda page=page: page)
