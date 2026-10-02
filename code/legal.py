"""Legal pages (Impressum, Datenschutz, Barrierefreiheit), served as plain HTML next to the Dash app.

Content follows the FAU central pages (fau.de/impressum, /datenschutz, /barrierefreiheit).
"""
from html import escape

# Person responsible for the content of this site (§ 18 Abs. 2 MStV) and contact for visitors.
CONTACT_NAME = "Oskar Herrmann"
CONTACT_EMAIL = "oskar.herrmann@fau.de"
CONTACT_INSTITUTE = "Institut für Geographie"
CONTACT_ADDRESS = "Wetterkreuz 15, 91058 Erlangen"

STATEMENT_DATE = "01.10.2026"

PAGES = {
    "impressum": "Impressum",
    "datenschutz": "Datenschutz",
    "barrierefreiheit": "Barrierefreiheit",
}

_contact = f"""<p>{escape(CONTACT_NAME)}<br>Friedrich-Alexander-Universität Erlangen-Nürnberg<br>
{escape(CONTACT_INSTITUTE)}<br>{escape(CONTACT_ADDRESS)}<br>
E-Mail: <a href="mailto:{escape(CONTACT_EMAIL)}">{escape(CONTACT_EMAIL)}</a></p>"""

_fau = """<p>Friedrich-Alexander-Universität Erlangen-Nürnberg (FAU)<br>Freyeslebenstraße 1<br>91058 Erlangen<br>
E-Mail: <a href="mailto:poststelle@fau.de">poststelle@fau.de</a></p>"""

BODIES = {
    "impressum": f"""
<h2>Anbieter</h2>
{_fau}
<p>Die Friedrich-Alexander-Universität Erlangen-Nürnberg ist gemäß Art. 4 Abs. 1 des Bayerischen
Hochschulinnovationsgesetzes (BayHIG) eine staatliche Einrichtung und zugleich eine Körperschaft des
öffentlichen Rechts. Sie wird gesetzlich vertreten durch den Präsidenten, Prof. Dr. Joachim Hornegger.</p>

<h2>Zuständige Aufsichtsbehörde</h2>
<p>Bayerisches Staatsministerium für Wissenschaft und Kunst<br>Salvatorstraße 2<br>80327 München<br>
E-Mail: <a href="mailto:poststelle@stmwk.bayern.de">poststelle@stmwk.bayern.de</a></p>

<h2>Umsatzsteuer-Identifikationsnummer</h2>
<p>DE 132507686</p>

<h2>Inhaltlich verantwortlich (§ 18 Abs. 2 MStV) und Kontakt</h2>
{_contact}

<h2>Daten und Quellen</h2>
<p>Gletscherumrisse: RGI 7.0 Consortium (2023): Randolph Glacier Inventory – A Dataset of Global Glacier
Outlines, Version 7.0. NSIDC, <a href="https://doi.org/10.5067/f6jmovy5navz">doi:10.5067/f6jmovy5navz</a>
(<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>).</p>
<p>Hintergrundkarte: Kartendaten © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>-Mitwirkende,
SRTM; Kartendarstellung © <a href="https://opentopomap.org">OpenTopoMap</a>
(<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>).</p>
<p>Gefördert durch die Europäische Union (Europäischer Forschungsrat, ERC). Die geäußerten Ansichten und
Meinungen sind ausschließlich die der Autorinnen und Autoren und spiegeln nicht unbedingt die der
Europäischen Union oder des Europäischen Forschungsrats wider.</p>

<h2>Haftung für Inhalte und Links</h2>
<p>Die dargestellten Gletscherentwicklungen sind Modellprojektionen und mit Unsicherheiten behaftet. Für
die Inhalte externer Seiten, auf die verwiesen wird, sind ausschließlich deren Betreiber verantwortlich.</p>
""",

    "datenschutz": f"""
<h2>Verantwortlicher</h2>
{_fau}
<p>Die FAU wird gesetzlich vertreten durch den Präsidenten, Prof. Dr. Joachim Hornegger.
Ansprechpartner für diese Website:</p>
{_contact}

<h2>Datenschutzbeauftragter</h2>
<p>Klaus Hoogestraat<br>c/o ITM Gesellschaft für IT-Management mbH<br>Bürgerstraße 81<br>01127 Dresden<br>
Telefon: +49 9131 85-25860<br>
E-Mail: <a href="mailto:datenschutzbeauftragter@fau.de">datenschutzbeauftragter@fau.de</a></p>

<h2>Server-Logdateien</h2>
<p>Bei jedem Aufruf dieser Website speichert der Webserver: IP-Adresse, Datum und Uhrzeit, aufgerufene
Adresse (einschließlich der gewählten Ansicht), HTTP-Statuscode, übertragene Datenmenge, Referrer-URL und
Browserkennung (User-Agent). Die Daten dienen dem sicheren Betrieb und der Fehleranalyse und werden nicht
mit anderen Daten zusammengeführt.</p>
<p>Rechtsgrundlage ist Art. 6 Abs. 1 UAbs. 1 lit. e DSGVO in Verbindung mit Art. 4 Abs. 1 BayDSG.
Die Logdateien werden nach 14 Tagen automatisch gelöscht.</p>

<h2>Kartendienste</h2>
<p>Die Hintergrundkarte von OpenTopoMap wird über unseren eigenen Server abgerufen und dort
zwischengespeichert. Dabei werden keine Daten über Sie an OpenTopoMap oder andere Dritte übermittelt.</p>

<h2>Lokaler Speicher, Cookies</h2>
<p>Diese Website setzt keine Cookies und verwendet keine Analyse- oder Tracking-Dienste. Die gewählte
Darstellung (hell/dunkel) wird im lokalen Speicher Ihres Browsers abgelegt und nicht an uns übertragen
(§ 25 Abs. 2 Nr. 2 TDDDG). Sie können sie jederzeit in Ihren Browsereinstellungen löschen.</p>

<h2>Ihre Rechte</h2>
<p>Sie haben das Recht auf Auskunft (Art. 15 DSGVO), Berichtigung (Art. 16), Löschung (Art. 17),
Einschränkung der Verarbeitung (Art. 18) und Widerspruch (Art. 21). Wenden Sie sich dazu an den
Verantwortlichen oder den Datenschutzbeauftragten.</p>
<p>Sie haben außerdem das Recht, sich bei der Aufsichtsbehörde zu beschweren:<br>
Der Bayerische Landesbeauftragte für den Datenschutz<br>Postfach 22 12 19<br>80502 München<br>
E-Mail: <a href="mailto:poststelle@datenschutz-bayern.de">poststelle@datenschutz-bayern.de</a><br>
<a href="https://www.datenschutz-bayern.de/">www.datenschutz-bayern.de</a></p>
""",

    "barrierefreiheit": f"""
<p>Die Friedrich-Alexander-Universität Erlangen-Nürnberg ist bemüht, ihre Websites im Einklang mit
Art. 13 BayBGG, Art. 1 BayBITV und der Richtlinie (EU) 2016/2102 barrierefrei zugänglich zu machen.
Diese Erklärung gilt für <a href="/">www.glacier-evolution.nat.fau.de</a>.</p>

<h2>Stand der Vereinbarkeit</h2>
<p>Diese Website ist wegen der folgenden Unvereinbarkeiten nur teilweise barrierefrei.</p>

<h2>Nicht barrierefreie Inhalte</h2>
<ul>
<li>Die 3D-Gletscheransicht und die Übersichtskarte sind interaktive Grafiken. Sie lassen sich nicht
  vollständig mit der Tastatur bedienen und werden von Screenreadern nicht als Text ausgegeben.</li>
<li>In Diagrammen und Karten werden Werte und Szenarien vor allem über Farben unterschieden.</li>
<li>Die Bedienoberfläche ist nur auf Englisch verfügbar; Inhalte in Leichter Sprache und
  Gebärdensprache werden nicht angeboten.</li>
</ul>
<p>Die Darstellung dreidimensionaler Modelldaten lässt sich nicht gleichwertig in Textform wiedergeben.
Die zugrunde liegenden Daten stellen wir auf Anfrage in tabellarischer Form zur Verfügung.</p>

<h2>Erstellung dieser Erklärung</h2>
<p>Diese Erklärung wurde am {STATEMENT_DATE} erstellt und beruht auf einer Selbstbewertung.</p>

<h2>Feedback und Kontakt</h2>
<p>Barrieren auf dieser Website oder Anfragen nach barrierefreien Inhalten können Sie uns melden:</p>
{_contact}

<h2>Durchsetzungsverfahren</h2>
<p>Erhalten Sie innerhalb von sechs Wochen keine zufriedenstellende Antwort, können Sie sich an die
Durchsetzungsstelle wenden:<br>
Landesamt für Digitalisierung, Breitband und Vermessung<br>IT-Dienstleistungszentrum des Freistaats Bayern<br>
Durchsetzungs- und Überwachungsstelle für barrierefreie Informationstechnik<br>
St.-Martin-Straße 47<br>81541 München<br>
E-Mail: <a href="mailto:bitv@bayern.de">bitv@bayern.de</a><br>
<a href="https://www.ldbv.bayern.de/digitalisierung/bitv/">www.ldbv.bayern.de/digitalisierung/bitv/</a></p>
""",
}

TEMPLATE = """<!DOCTYPE html>
<html lang="de" data-theme="dark">
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
<p><a href="/">← Zurück zum Dashboard</a></p>
<h1>{title}</h1>
{body}
<nav class="legal-links">{nav}</nav>
</main>
</body>
</html>"""


def legal_links():
    """Footer links as Dash components."""
    from dash import html
    return html.Nav(className="legal-links",
                    children=[html.A(title, href=f"/{slug}") for slug, title in PAGES.items()])


def register(server, css_url):
    nav = "".join(f'<a href="/{slug}">{title}</a>' for slug, title in PAGES.items())
    for slug, title in PAGES.items():
        page = TEMPLATE.format(title=title, css=css_url, body=BODIES[slug], nav=nav)
        server.add_url_rule(f"/{slug}", endpoint=f"legal_{slug}", view_func=lambda page=page: page)
