"""Configuration for the Google Maps lead scraper."""

import json
import sys
from pathlib import Path

# Add Bots folder to path so common module can be found
_SCRAPER_DIR = Path(__file__).parent
_BOTS_DIR = _SCRAPER_DIR.parent
if str(_BOTS_DIR) not in sys.path:
    sys.path.insert(0, str(_BOTS_DIR))

CONFIG_OVERRIDES_FILE = _SCRAPER_DIR / "config_overrides.json"


def _read_overrides() -> dict:
    if not CONFIG_OVERRIDES_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_OVERRIDES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_overrides(data: dict) -> None:
    tmp = CONFIG_OVERRIDES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_OVERRIDES_FILE)


def _as_int(value, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _as_float(value, default: float, minimum: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _clean_string_list(values) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for item in values:
        text = str(item).strip()
        if text:
            cleaned.append(text)
    return cleaned


def _clean_keyword_categories(values, fallback: list[list[str]]) -> list[list[str]]:
    if not isinstance(values, list):
        return [list(group) for group in fallback]

    cleaned: list[list[str]] = []
    for idx in range(4):
        group = values[idx] if idx < len(values) else []
        parsed = _clean_string_list(group if isinstance(group, list) else [])
        cleaned.append(parsed or list(fallback[idx]))
    return cleaned


# ============================================================================
# LOCATIONS
# ============================================================================
# Narrow geographic targets – add more items as your addressing partner
# delivers additional micro-locations.

_FULL_LOCATIONS = [
  "Aalsmeerwegbuurt Oost, Amsterdam, Netherlands",
  "Aalsmeerwegbuurt West, Amsterdam, Netherlands",
  "Afrikahaven, Amsterdam, Netherlands",
  "Alexanderplein e.o., Amsterdam, Netherlands",
  "Alfa-driehoek, Amsterdam, Netherlands",
  "AMC, Amsterdam, Netherlands",
  "Amerikahaven, Amsterdam, Netherlands",
  "Amstel III deel A/B Noord, Amsterdam, Netherlands",
  "Amstel III deel A/B Zuid, Amsterdam, Netherlands",
  "Amstel III deel C/D Noord, Amsterdam, Netherlands",
  "Amstel III deel C/D Zuid, Amsterdam, Netherlands",
  "Amstelglorie, Amsterdam, Netherlands",
  "Amstelkwartier Noord, Amsterdam, Netherlands",
  "Amstelkwartier West, Amsterdam, Netherlands",
  "Amstelkwartier Zuid, Amsterdam, Netherlands",
  "Amstelpark, Amsterdam, Netherlands",
  "Amstelveldbuurt, Amsterdam, Netherlands",
  "Amsterdamse Bos, Amsterdam, Netherlands",
  "Amsterdamse Poort, Amsterdam, Netherlands",
  "Andreasterrein, Amsterdam, Netherlands",
  "Anjeliersbuurt Noord, Amsterdam, Netherlands",
  "Anjeliersbuurt Zuid, Amsterdam, Netherlands",
  "Architectenbuurt, Amsterdam, Netherlands",
  "Baanakkerspark Noord, Amsterdam, Netherlands",
  "Baanakkerspark Zuid, Amsterdam, Netherlands",
  "Balboaplein e.o., Amsterdam, Netherlands",
  "Banne Noordoost, Amsterdam, Netherlands",
  "Banne Noordwest, Amsterdam, Netherlands",
  "Banne Zuidoost, Amsterdam, Netherlands",
  "Banne Zuidwest, Amsterdam, Netherlands",
  "Banpleinbuurt, Amsterdam, Netherlands",
  "Beatrixpark, Amsterdam, Netherlands",
  "Bedrijvencentrum Osdorp, Amsterdam, Netherlands",
  "Bedrijvencentrum Westerkwartier, Amsterdam, Netherlands",
  "Bedrijvengebied Cruquiusweg, Amsterdam, Netherlands",
  "Bedrijvengebied Veelaan, Amsterdam, Netherlands",
  "Bedrijvengebied Zeeburgerkade, Amsterdam, Netherlands",
  "Bedrijvenpark Lutkemeer, Amsterdam, Netherlands",
  "Bedrijventerrein Hamerstraat, Amsterdam, Netherlands",
  "Bedrijventerrein Landlust, Amsterdam, Netherlands",
  "Bedrijventerrein Nieuwendammerdijk, Amsterdam, Netherlands",
  "Bedrijventerrein Schinkel, Amsterdam, Netherlands",
  "Bedrijventerrein Sloterdijk I, Amsterdam, Netherlands",
  "Beethovenbuurt, Amsterdam, Netherlands",
  "Begijnhofbuurt, Amsterdam, Netherlands",
  "Belgi\u00ebplein e.o., Amsterdam, Netherlands",
  "Bellamybuurt Noord, Amsterdam, Netherlands",
  "Bellamybuurt Zuid, Amsterdam, Netherlands",
  "Bertelmanpleinbuurt, Amsterdam, Netherlands",
  "Betondorp, Amsterdam, Netherlands",
  "BG-terrein e.o., Amsterdam, Netherlands",
  "Bijlmermuseum Noord, Amsterdam, Netherlands",
  "Bijlmermuseum Zuid, Amsterdam, Netherlands",
  "Blauwe Zand, Amsterdam, Netherlands",
  "Bloemenbuurt Noord, Amsterdam, Netherlands",
  "Bloemenbuurt Zuid, Amsterdam, Netherlands",
  "Bloemgrachtbuurt, Amsterdam, Netherlands",
  "Borgerbuurt, Amsterdam, Netherlands",
  "Borneo, Amsterdam, Netherlands",
  "Bosleeuw, Amsterdam, Netherlands",
  "Bretten Oost, Amsterdam, Netherlands",
  "Bretten West, Amsterdam, Netherlands",
  "Buiksloterbreek, Amsterdam, Netherlands",
  "Buiksloterdijk Oost, Amsterdam, Netherlands",
  "Buiksloterdijk West, Amsterdam, Netherlands",
  "Buiksloterham, Amsterdam, Netherlands",
  "Buikslotermeer Noord, Amsterdam, Netherlands",
  "Buikslotermeerplein, Amsterdam, Netherlands",
  "Buiteneiland, Amsterdam, Netherlands",
  "Buitenveldert Midden Zuid, Amsterdam, Netherlands",
  "Buitenveldert Oost Midden, Amsterdam, Netherlands",
  "Buitenveldert West Midden, Amsterdam, Netherlands",
  "Buitenveldert Zuidoost, Amsterdam, Netherlands",
  "Buitenveldert Zuidwest, Amsterdam, Netherlands",
  "Burgemeester Tellegenbuurt Oost, Amsterdam, Netherlands",
  "Burgemeester Tellegenbuurt West, Amsterdam, Netherlands",
  "Burgwallen Oost, Amsterdam, Netherlands",
  "Buurt 10, Amsterdam, Netherlands",
  "Buurt 2, Amsterdam, Netherlands",
  "Buurt 3, Amsterdam, Netherlands",
  "Buurt 4 Oost, Amsterdam, Netherlands",
  "Buurt 5 Noord, Amsterdam, Netherlands",
  "Buurt 5 Zuid, Amsterdam, Netherlands",
  "Buurt 6, Amsterdam, Netherlands",
  "Buurt 7, Amsterdam, Netherlands",
  "Buurt 8, Amsterdam, Netherlands",
  "Buurt 9, Amsterdam, Netherlands",
  "Buyskade e.o., Amsterdam, Netherlands",
  "Calandlaan/Lelylaan, Amsterdam, Netherlands",
  "Centrumeiland, Amsterdam, Netherlands",
  "Circus/Kermisbuurt, Amsterdam, Netherlands",
  "Coenhaven/Mercuriushaven, Amsterdam, Netherlands",
  "Columbusplein e.o., Amsterdam, Netherlands",
  "Concertgebouwbuurt, Amsterdam, Netherlands",
  "Cornelis Douwesterrein, Amsterdam, Netherlands",
  "Cornelis Schuytbuurt, Amsterdam, Netherlands",
  "Cornelis Troostbuurt, Amsterdam, Netherlands",
  "Cremerbuurt Oost, Amsterdam, Netherlands",
  "Cremerbuurt West, Amsterdam, Netherlands",
  "Czaar Peterbuurt, Amsterdam, Netherlands",
  "D-buurt, Amsterdam, Netherlands",
  "Da Costabuurt Noord, Amsterdam, Netherlands",
  "Da Costabuurt Zuid, Amsterdam, Netherlands",
  "Dapperbuurt Noord, Amsterdam, Netherlands",
  "Dapperbuurt Zuid, Amsterdam, Netherlands",
  "De Aker Oost, Amsterdam, Netherlands",
  "De Aker West, Amsterdam, Netherlands",
  "De Bongerd, Amsterdam, Netherlands",
  "De Eenhoorn, Amsterdam, Netherlands",
  "De Heining, Amsterdam, Netherlands",
  "De Kleine Wereld, Amsterdam, Netherlands",
  "De Klenckebuurt, Amsterdam, Netherlands",
  "De Omval, Amsterdam, Netherlands",
  "de Plantage, Amsterdam, Netherlands",
  "De Punt, Amsterdam, Netherlands",
  "De Wester Quartier, Amsterdam, Netherlands",
  "De Wetbuurt, Amsterdam, Netherlands",
  "De Wittenbuurt Noord, Amsterdam, Netherlands",
  "De Wittenbuurt Zuid, Amsterdam, Netherlands",
  "Delflandpleinbuurt Oost, Amsterdam, Netherlands",
  "Delflandpleinbuurt West, Amsterdam, Netherlands",
  "Den Texbuurt, Amsterdam, Netherlands",
  "Diamantbuurt, Amsterdam, Netherlands",
  "Diepenbrockbuurt, Amsterdam, Netherlands",
  "Don Bosco, Amsterdam, Netherlands",
  "Dorp Driemond, Amsterdam, Netherlands",
  "Dorp Sloten, Amsterdam, Netherlands",
  "Drieburg, Amsterdam, Netherlands",
  "Driehoekbuurt, Amsterdam, Netherlands",
  "Duivelseiland, Amsterdam, Netherlands",
  "Durgerdam, Amsterdam, Netherlands",
  "E-buurt, Amsterdam, Netherlands",
  "Ecowijk, Amsterdam, Netherlands",
  "Eendrachtspark, Amsterdam, Netherlands",
  "Elandsgrachtbuurt, Amsterdam, Netherlands",
  "Elzenhagen Noord, Amsterdam, Netherlands",
  "Elzenhagen Zuid, Amsterdam, Netherlands",
  "Emanuel van Meterenbuurt, Amsterdam, Netherlands",
  "Entrepot-Noordwest, Amsterdam, Netherlands",
  "Erasmusparkbuurt Oost, Amsterdam, Netherlands",
  "Erasmusparkbuurt West, Amsterdam, Netherlands",
  "F-buurt, Amsterdam, Netherlands",
  "Fannius Scholtenbuurt, Amsterdam, Netherlands",
  "Felix Meritisbuurt, Amsterdam, Netherlands",
  "Filips van Almondekwartier, Amsterdam, Netherlands",
  "Flevopark, Amsterdam, Netherlands",
  "Frankendael, Amsterdam, Netherlands",
  "Frans Halsbuurt, Amsterdam, Netherlands",
  "Frederik Hendrikbuurt Noord, Amsterdam, Netherlands",
  "Frederik Hendrikbuurt Zuidoost, Amsterdam, Netherlands",
  "Frederik Hendrikbuurt Zuidwest, Amsterdam, Netherlands",
  "Frederikspleinbuurt, Amsterdam, Netherlands",
  "G-buurt Noord, Amsterdam, Netherlands",
  "G-buurt Oost, Amsterdam, Netherlands",
  "G-buurt West, Amsterdam, Netherlands",
  "Gaasperdam Noord, Amsterdam, Netherlands",
  "Gaasperdam Zuid, Amsterdam, Netherlands",
  "Gaasperpark, Amsterdam, Netherlands",
  "Gaasperplas, Amsterdam, Netherlands",
  "Gein Noordoost, Amsterdam, Netherlands",
  "Gein Noordwest, Amsterdam, Netherlands",
  "Gein Zuidoost, Amsterdam, Netherlands",
  "Gein Zuidwest, Amsterdam, Netherlands",
  "Gelderlandpleinbuurt, Amsterdam, Netherlands",
  "Gerard Doubuurt, Amsterdam, Netherlands",
  "Geuzenhofbuurt, Amsterdam, Netherlands",
  "Gibraltarbuurt, Amsterdam, Netherlands",
  "Gooise Kant, Amsterdam, Netherlands",
  "Gouden Bocht, Amsterdam, Netherlands",
  "Groenmarktkadebuurt, Amsterdam, Netherlands",
  "Grunder/Koningshoef, Amsterdam, Netherlands",
  "Haarlemmerbuurt Oost, Amsterdam, Netherlands",
  "Haarlemmerbuurt West, Amsterdam, Netherlands",
  "Hakfort/Huigenbos, Amsterdam, Netherlands",
  "Harmoniehofbuurt, Amsterdam, Netherlands",
  "Haveneiland Noord, Amsterdam, Netherlands",
  "Haveneiland Noordoost, Amsterdam, Netherlands",
  "Haveneiland Noordwest, Amsterdam, Netherlands",
  "Haveneiland Oost, Amsterdam, Netherlands",
  "Haveneiland Zuidwest/Rieteiland West, Amsterdam, Netherlands",
  "Helmersbuurt Oost, Amsterdam, Netherlands",
  "Hemelrijk, Amsterdam, Netherlands",
  "Hemonybuurt, Amsterdam, Netherlands",
  "Hercules Seghersbuurt, Amsterdam, Netherlands",
  "Het Funen, Amsterdam, Netherlands",
  "Hiltonbuurt, Amsterdam, Netherlands",
  "Hoge Dijk, Amsterdam, Netherlands",
  "Holendrecht Oost, Amsterdam, Netherlands",
  "Holendrecht West, Amsterdam, Netherlands",
  "Holysloot, Amsterdam, Netherlands",
  "Hondecoeterbuurt, Amsterdam, Netherlands",
  "Hoofdcentrum Zuidoost, Amsterdam, Netherlands",
  "Hoptille, Amsterdam, Netherlands",
  "Houthavens Oost, Amsterdam, Netherlands",
  "Houthavens West, Amsterdam, Netherlands",
  "Huntum, Amsterdam, Netherlands",
  "IJplein e.o., Amsterdam, Netherlands",
  "IJsbaanpad e.o., Amsterdam, Netherlands",
  "IJselbuurt Oost, Amsterdam, Netherlands",
  "IJselbuurt West, Amsterdam, Netherlands",
  "Jacob Geelbuurt, Amsterdam, Netherlands",
  "Jacques Veltmanbuurt, Amsterdam, Netherlands",
  "Jan Maijenbuurt, Amsterdam, Netherlands",
  "Java-eiland, Amsterdam, Netherlands",
  "Johan Jongkindbuurt, Amsterdam, Netherlands",
  "Johannnes Vermeerbuurt, Amsterdam, Netherlands",
  "John Franklinbuurt, Amsterdam, Netherlands",
  "Julianapark, Amsterdam, Netherlands",
  "K-buurt Midden, Amsterdam, Netherlands",
  "K-buurt Zuidoost, Amsterdam, Netherlands",
  "K-buurt Zuidwest, Amsterdam, Netherlands",
  "Kadijken, Amsterdam, Netherlands",
  "Kadoelen, Amsterdam, Netherlands",
  "Kalverdriehoek, Amsterdam, Netherlands",
  "Kantershof, Amsterdam, Netherlands",
  "Kattenburg, Amsterdam, Netherlands",
  "Kazernebuurt, Amsterdam, Netherlands",
  "Kelbergen, Amsterdam, Netherlands",
  "KNSM-eiland, Amsterdam, Netherlands",
  "Kolenkitbuurt Noord, Amsterdam, Netherlands",
  "Kolenkitbuurt Zuid, Amsterdam, Netherlands",
  "Koningin Wilhelminaplein, Amsterdam, Netherlands",
  "Kop Zeedijk, Amsterdam, Netherlands",
  "Kop Zuidas, Amsterdam, Netherlands",
  "Kortenaerkwartier, Amsterdam, Netherlands",
  "Kortvoort, Amsterdam, Netherlands",
  "Kromme Mijdrechtbuurt, Amsterdam, Netherlands",
  "L-buurt, Amsterdam, Netherlands",
  "Laan van Spartaan, Amsterdam, Netherlands",
  "Landelijk gebied Driemond, Amsterdam, Netherlands",
  "Landlust Noord, Amsterdam, Netherlands",
  "Landlust Zuid, Amsterdam, Netherlands",
  "Langestraat e.o., Amsterdam, Netherlands",
  "Lastage, Amsterdam, Netherlands",
  "Legmeerpleinbuurt, Amsterdam, Netherlands",
  "Leidsebuurt Noordoost, Amsterdam, Netherlands",
  "Leidsebuurt Noordwest, Amsterdam, Netherlands",
  "Leidsebuurt Zuidoost, Amsterdam, Netherlands",
  "Leidsebuurt Zuidwest, Amsterdam, Netherlands",
  "Leidsegracht Noord, Amsterdam, Netherlands",
  "Leidsegracht Zuid, Amsterdam, Netherlands",
  "Leliegracht e.o., Amsterdam, Netherlands",
  "Linnaeusparkbuurt, Amsterdam, Netherlands",
  "Lizzy Ansinghbuurt, Amsterdam, Netherlands",
  "Loenermark, Amsterdam, Netherlands",
  "Lootsbuurt, Amsterdam, Netherlands",
  "Louis Chrispijnbuurt, Amsterdam, Netherlands",
  "Lucas/Andreasziekenhuis e.o., Amsterdam, Netherlands",
  "Marathonbuurt Oost, Amsterdam, Netherlands",
  "Marathonbuurt West, Amsterdam, Netherlands",
  "Marcanti, Amsterdam, Netherlands",
  "Marine-Etablissement, Amsterdam, Netherlands",
  "Marjoleinterrein, Amsterdam, Netherlands",
  "Markengouw Midden, Amsterdam, Netherlands",
  "Markengouw Zuid, Amsterdam, Netherlands",
  "Markthallen, Amsterdam, Netherlands",
  "Marnixbuurt Midden, Amsterdam, Netherlands",
  "Marnixbuurt Noord, Amsterdam, Netherlands",
  "Marnixbuurt Zuid, Amsterdam, Netherlands",
  "Medisch Centrum Slotervaart, Amsterdam, Netherlands",
  "Meer en Oever, Amsterdam, Netherlands",
  "Mercatorpark, Amsterdam, Netherlands",
  "Middelveldsche Akerpolder, Amsterdam, Netherlands",
  "Middeneiland Zuidoost, Amsterdam, Netherlands",
  "Middeneiland Zuidwest, Amsterdam, Netherlands",
  "Middenmeer Noord, Amsterdam, Netherlands",
  "Middenmeer Zuid, Amsterdam, Netherlands",
  "Minervabuurt Midden, Amsterdam, Netherlands",
  "Minervabuurt Noord, Amsterdam, Netherlands",
  "Minervabuurt Zuid, Amsterdam, Netherlands",
  "Molenwijk, Amsterdam, Netherlands",
  "Museumplein, Amsterdam, Netherlands",
  "NDSM terrein, Amsterdam, Netherlands",
  "Nelson Mandelapark, Amsterdam, Netherlands",
  "Nes e.o., Amsterdam, Netherlands",
  "Nieuw Sloten Noordoost, Amsterdam, Netherlands",
  "Nieuw Sloten Noordwest, Amsterdam, Netherlands",
  "Nieuw Sloten Zuidoost, Amsterdam, Netherlands",
  "Nieuw Sloten Zuidwest, Amsterdam, Netherlands",
  "Nieuwe Diep/Diemerpark, Amsterdam, Netherlands",
  "Nieuwe Kerk e.o., Amsterdam, Netherlands",
  "Nieuwe Meer, Amsterdam, Netherlands",
  "Nieuwe Oosterbegraafplaats, Amsterdam, Netherlands",
  "Nieuwendammerdijk Oost, Amsterdam, Netherlands",
  "Nieuwendammerdijk West, Amsterdam, Netherlands",
  "Nieuwendammerdijk Zuid, Amsterdam, Netherlands",
  "Nieuwendijk Noord, Amsterdam, Netherlands",
  "Nieuwmarkt, Amsterdam, Netherlands",
  "Nintemanterrein, Amsterdam, Netherlands",
  "Noorder IJplas, Amsterdam, Netherlands",
  "Noorderstrook Oost, Amsterdam, Netherlands",
  "Noorderstrook West, Amsterdam, Netherlands",
  "Noordoever Sloterplas, Amsterdam, Netherlands",
  "Noordoostkwadrant Indische buurt, Amsterdam, Netherlands",
  "Noordwestkwadrant Indische buurt Noord, Amsterdam, Netherlands",
  "Noordwestkwadrant Indische buurt Zuid, Amsterdam, Netherlands",
  "Olympisch Stadion e.o., Amsterdam, Netherlands",
  "Ookmeer, Amsterdam, Netherlands",
  "Oostelijke Handelskade, Amsterdam, Netherlands",
  "Oostenburg, Amsterdam, Netherlands",
  "Oosterdokseiland, Amsterdam, Netherlands",
  "Oosterpark, Amsterdam, Netherlands",
  "Oosterparkbuurt Noordwest, Amsterdam, Netherlands",
  "Oosterparkbuurt Zuidoost, Amsterdam, Netherlands",
  "Oosterparkbuurt Zuidwest, Amsterdam, Netherlands",
  "Oostoever Sloterplas, Amsterdam, Netherlands",
  "Oostpoort, Amsterdam, Netherlands",
  "Oostzanerdijk, Amsterdam, Netherlands",
  "Orteliusbuurt Midden, Amsterdam, Netherlands",
  "Orteliusbuurt Noord, Amsterdam, Netherlands",
  "Orteliusbuurt Zuid, Amsterdam, Netherlands",
  "Osdorp Midden Noord, Amsterdam, Netherlands",
  "Osdorp Midden Zuid, Amsterdam, Netherlands",
  "Osdorp Zuidoost, Amsterdam, Netherlands",
  "Osdorper Binnenpolder, Amsterdam, Netherlands",
  "Osdorper Bovenpolder, Amsterdam, Netherlands",
  "Osdorpplein e.o., Amsterdam, Netherlands",
  "Oude Kerk e.o., Amsterdam, Netherlands",
  "Overamstel, Amsterdam, Netherlands",
  "Overbraker Binnenpolder, Amsterdam, Netherlands",
  "Overhoeks, Amsterdam, Netherlands",
  "Overtoomse Veld Noord, Amsterdam, Netherlands",
  "Overtoomse Veld Zuid, Amsterdam, Netherlands",
  "P.C. Hooftbuurt, Amsterdam, Netherlands",
  "Papaverweg e.o., Amsterdam, Netherlands",
  "Paramariboplein e.o., Amsterdam, Netherlands",
  "Park de Meer, Amsterdam, Netherlands",
  "Park Haagseweg, Amsterdam, Netherlands",
  "Parooldriehoek, Amsterdam, Netherlands",
  "Passeerdersgrachtbuurt, Amsterdam, Netherlands",
  "Petroleumhaven, Amsterdam, Netherlands",
  "Pieter van der Doesbuurt, Amsterdam, Netherlands",
  "Plan van Gool, Amsterdam, Netherlands",
  "Planciusbuurt Noord, Amsterdam, Netherlands",
  "Planciusbuurt Zuid, Amsterdam, Netherlands",
  "Postjeskade e.o., Amsterdam, Netherlands",
  "Prinses Irenebuurt, Amsterdam, Netherlands",
  "RAI, Amsterdam, Netherlands",
  "Ransdorp, Amsterdam, Netherlands",
  "Rapenburg, Amsterdam, Netherlands",
  "Rechte H-buurt, Amsterdam, Netherlands",
  "Reguliersbuurt, Amsterdam, Netherlands",
  "Reigersbos Midden, Amsterdam, Netherlands",
  "Reigersbos Noord, Amsterdam, Netherlands",
  "Reigersbos Zuid, Amsterdam, Netherlands",
  "Rembrandtpark Noord, Amsterdam, Netherlands",
  "Rembrandtpark Zuid, Amsterdam, Netherlands",
  "Rembrandtpleinbuurt, Amsterdam, Netherlands",
  "RI Oost terrein, Amsterdam, Netherlands",
  "Riekerhaven, Amsterdam, Netherlands",
  "Riekerpolder, Amsterdam, Netherlands",
  "Rieteiland Oost, Amsterdam, Netherlands",
  "Rietlanden, Amsterdam, Netherlands",
  "Rijnbuurt Midden, Amsterdam, Netherlands",
  "Rijnbuurt Oost, Amsterdam, Netherlands",
  "Rijnbuurt West, Amsterdam, Netherlands",
  "Robert Scottbuurt Oost, Amsterdam, Netherlands",
  "Robert Scottbuurt West, Amsterdam, Netherlands",
  "Rode Kruisbuurt, Amsterdam, Netherlands",
  "Sarphatiparkbuurt, Amsterdam, Netherlands",
  "Sarphatistrook, Amsterdam, Netherlands",
  "Scheepvaarthuisbuurt, Amsterdam, Netherlands",
  "Scheldebuurt Midden, Amsterdam, Netherlands",
  "Scheldebuurt Oost, Amsterdam, Netherlands",
  "Scheldebuurt West, Amsterdam, Netherlands",
  "Schellingwoude Noord, Amsterdam, Netherlands",
  "Schellingwoude Oost, Amsterdam, Netherlands",
  "Schellingwoude West, Amsterdam, Netherlands",
  "Schinkelbuurt Noord, Amsterdam, Netherlands",
  "Schinkelbuurt Zuid, Amsterdam, Netherlands",
  "Schipluidenbuurt, Amsterdam, Netherlands",
  "Science Park Noord, Amsterdam, Netherlands",
  "Science Park Zuid, Amsterdam, Netherlands",
  "Sloterdijk II, Amsterdam, Netherlands",
  "Sloterdijk III Oost, Amsterdam, Netherlands",
  "Sloterdijk III West, Amsterdam, Netherlands",
  "Slotermeer Zuid, Amsterdam, Netherlands",
  "Sloterpark, Amsterdam, Netherlands",
  "Sloterweg e.o., Amsterdam, Netherlands",
  "Spaarndammerbuurt Midden, Amsterdam, Netherlands",
  "Spaarndammerbuurt Noordoost, Amsterdam, Netherlands",
  "Spaarndammerbuurt Noordwest, Amsterdam, Netherlands",
  "Spaarndammerbuurt Zuidoost, Amsterdam, Netherlands",
  "Spaarndammerbuurt Zuidwest, Amsterdam, Netherlands",
  "Spiegelbuurt, Amsterdam, Netherlands",
  "Sporenburg, Amsterdam, Netherlands",
  "Sportpark Middenmeer Noord, Amsterdam, Netherlands",
  "Sportpark Middenmeer Zuid, Amsterdam, Netherlands",
  "Sportpark Voorland, Amsterdam, Netherlands",
  "Spuistraat Noord, Amsterdam, Netherlands",
  "Spuistraat Zuid, Amsterdam, Netherlands",
  "Staalmanbuurt, Amsterdam, Netherlands",
  "Staatsliedenbuurt Noordoost, Amsterdam, Netherlands",
  "Stationsplein e.o., Amsterdam, Netherlands",
  "Steigereiland Noord, Amsterdam, Netherlands",
  "Steigereiland Zuid, Amsterdam, Netherlands",
  "Strandeiland, Amsterdam, Netherlands",
  "Surinamepleinbuurt, Amsterdam, Netherlands",
  "Swammerdambuurt, Amsterdam, Netherlands",
  "Teleport, Amsterdam, Netherlands",
  "Terrasdorp, Amsterdam, Netherlands",
  "Transvaalbuurt Oost, Amsterdam, Netherlands",
  "Transvaalbuurt West, Amsterdam, Netherlands",
  "Trompbuurt, Amsterdam, Netherlands",
  "Tuindorp Amstelstation, Amsterdam, Netherlands",
  "Tuindorp Frankendael, Amsterdam, Netherlands",
  "Tuindorp Nieuwendam Oost, Amsterdam, Netherlands",
  "Tuindorp Nieuwendam West, Amsterdam, Netherlands",
  "Tuindorp Oostzaan Oost, Amsterdam, Netherlands",
  "Tuindorp Oostzaan West, Amsterdam, Netherlands",
  "Twiske Oost, Amsterdam, Netherlands",
  "Twiske West, Amsterdam, Netherlands",
  "Uilenburg, Amsterdam, Netherlands",
  "Utrechtsebuurt Zuid, Amsterdam, Netherlands",
  "Valeriusbuurt Oost, Amsterdam, Netherlands",
  "Valeriusbuurt West, Amsterdam, Netherlands",
  "Valkenburg, Amsterdam, Netherlands",
  "Van Brakelkwartier, Amsterdam, Netherlands",
  "Van der Helstpleinbuurt, Amsterdam, Netherlands",
  "Van der Kunbuurt, Amsterdam, Netherlands",
  "Van der Pekbuurt, Amsterdam, Netherlands",
  "Van Loonbuurt, Amsterdam, Netherlands",
  "Van Tuyllbuurt, Amsterdam, Netherlands",
  "Veluwebuurt, Amsterdam, Netherlands",
  "Venserpolder Oost, Amsterdam, Netherlands",
  "Venserpolder West, Amsterdam, Netherlands",
  "Vervoerscentrum, Amsterdam, Netherlands",
  "Vivaldi, Amsterdam, Netherlands",
  "Vliegenbos, Amsterdam, Netherlands",
  "Vogelbuurt Noord, Amsterdam, Netherlands",
  "Vogelbuurt Zuid, Amsterdam, Netherlands",
  "Vogeltjeswei, Amsterdam, Netherlands",
  "Vondelpark Oost, Amsterdam, Netherlands",
  "Vondelpark West, Amsterdam, Netherlands",
  "Vondelparkbuurt Midden, Amsterdam, Netherlands",
  "Vondelparkbuurt Oost, Amsterdam, Netherlands",
  "Vondelparkbuurt West, Amsterdam, Netherlands",
  "VU-kwartier, Amsterdam, Netherlands",
  "Walvisbuurt, Amsterdam, Netherlands",
  "Waterloopleinbuurt, Amsterdam, Netherlands",
  "Weesperbuurt, Amsterdam, Netherlands",
  "Weespertrekvaart, Amsterdam, Netherlands",
  "Weesperzijde Midden/Zuid, Amsterdam, Netherlands",
  "Werengouw Midden, Amsterdam, Netherlands",
  "Werengouw Zuid, Amsterdam, Netherlands",
  "Westelijke eilanden, Amsterdam, Netherlands",
  "Westerdokseiland, Amsterdam, Netherlands",
  "Westergasfabriek, Amsterdam, Netherlands",
  "Westerstaatsman, Amsterdam, Netherlands",
  "Westhaven Noord, Amsterdam, Netherlands",
  "Westhaven Zuid, Amsterdam, Netherlands",
  "Westlandgrachtbuurt, Amsterdam, Netherlands",
  "Weteringbuurt, Amsterdam, Netherlands",
  "WG-terrein, Amsterdam, Netherlands",
  "Wielingenbuurt, Amsterdam, Netherlands",
  "Wildeman, Amsterdam, Netherlands",
  "Willemsparkbuurt Noord, Amsterdam, Netherlands",
  "Willibrordusbuurt, Amsterdam, Netherlands",
  "Wittenburg, Amsterdam, Netherlands",
  "Woon- en Groengebied Sloterdijk, Amsterdam, Netherlands",
  "Zaagpoortbuurt, Amsterdam, Netherlands",
  "Zamenhofstraat e.o., Amsterdam, Netherlands",
  "Zeeburgerdijk Oost, Amsterdam, Netherlands",
  "Zeeburgereiland Noordoost, Amsterdam, Netherlands",
  "Zeeburgereiland Noordwest, Amsterdam, Netherlands",
  "Zeeburgereiland Zuidoost, Amsterdam, Netherlands",
  "Zeeburgereiland Zuidwest, Amsterdam, Netherlands",
  "Zeeheldenbuurt, Amsterdam, Netherlands",
  "Zorgvlied, Amsterdam, Netherlands",
  "Zuidas Noord, Amsterdam, Netherlands",
  "Zuidas Zuid, Amsterdam, Netherlands",
  "Zuiderhof, Amsterdam, Netherlands",
  "Zuiderkerkbuurt, Amsterdam, Netherlands",
  "Zuidoostkwadrant Indische buurt, Amsterdam, Netherlands",
  "Zuidwestkwadrant Indische buurt, Amsterdam, Netherlands",
  "Zuidwestkwadrant Osdorp Noord, Amsterdam, Netherlands",
  "Zuidwestkwadrant Osdorp Zuid, Amsterdam, Netherlands",
  "Zunderdorp, Amsterdam, Netherlands",
  "Zwarte Gouw, Amsterdam, Netherlands"
]

# Public alias for locations (used by progress_status.py)
_DEFAULT_LOCATIONS = list(_FULL_LOCATIONS)

# Keywords organized by category - scraper processes one category at a time.
# Use --next-category to advance to the next category.

KEYWORD_CATEGORY_1 = [
    "proprietary trading",
    "algorithmic trading",
    "quantitative trading",
    "financial trading",
]

KEYWORD_CATEGORY_2 = [
    "family office",
    "wealth management",
    "asset manager",
    "private equity",
    "venture capital",
    "investment fund",
]

KEYWORD_CATEGORY_3 = [
    "law firm",
    "corporate lawyer",
    "business attorney",
    "tax advisor",
]

KEYWORD_CATEGORY_4 = [
    "insurance firm",
    "insurance brokerage",
    "risk advisory",
    "real estate investment",
    "real estate fund",
]

# All categories in processing order
_DEFAULT_KEYWORD_CATEGORIES = [
    KEYWORD_CATEGORY_1,
    KEYWORD_CATEGORY_2,
    KEYWORD_CATEGORY_3,
    KEYWORD_CATEGORY_4,
]

_OVERRIDES = _read_overrides()

LOCATION_SOURCE_MODE_INLINE = "inline"
LOCATION_SOURCE_MODE_SHEET = "sheet_csv"


def _clean_location_source(value: dict | None) -> dict:
    data = value if isinstance(value, dict) else {}
    mode = str(data.get("mode") or LOCATION_SOURCE_MODE_INLINE).strip().lower()
    if mode not in {LOCATION_SOURCE_MODE_INLINE, LOCATION_SOURCE_MODE_SHEET}:
        mode = LOCATION_SOURCE_MODE_INLINE
    tab_names = _clean_string_list(data.get("tab_names"))
    legacy_tab_name = str(data.get("tab_name") or "").strip()
    if not tab_names and legacy_tab_name:
        tab_names = [legacy_tab_name]
    active_tab_index = _as_int(data.get("active_tab_index"), 0, minimum=0)
    return {
        "mode": mode,
        "sheet_url": str(data.get("sheet_url") or "").strip(),
        "tab_name": legacy_tab_name,
        "tab_names": tab_names,
        "active_tab_index": active_tab_index,
        "active_tab_name": tab_names[active_tab_index] if tab_names and active_tab_index < len(tab_names) else (tab_names[0] if tab_names else legacy_tab_name),
        "auto_advance_enabled": _as_bool(data.get("auto_advance_enabled"), False),
        "auto_advance_delay_minutes": _as_int(data.get("auto_advance_delay_minutes"), 30, minimum=1),
        "column_name": str(data.get("column_name") or "").strip(),
        "last_synced_at": str(data.get("last_synced_at") or "").strip(),
        "csv_export_url": str(data.get("csv_export_url") or "").strip(),
    }


def _resolve_locations(overrides: dict) -> list[str]:
    source = _clean_location_source(overrides.get("location_source"))
    inline_locations = _clean_string_list(overrides.get("locations")) or _DEFAULT_LOCATIONS
    resolved_locations = _clean_string_list(overrides.get("location_resolved_locations"))
    if source["mode"] == LOCATION_SOURCE_MODE_SHEET and resolved_locations:
        return resolved_locations
    return inline_locations


def get_locations() -> list[str]:
    """Return the current effective location list from the latest overrides."""
    return _resolve_locations(_read_overrides())


def _sheet_client():
    import gspread
    from google.oauth2.service_account import Credentials
    import os

    creds_path = os.path.expanduser(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "~/sheet-bot-key.json"))
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    return gspread.authorize(creds)


def _extract_sheet_id(sheet_url: str) -> str:
    import re

    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", sheet_url or "")
    if not match:
        raise ValueError("Invalid Google Sheet URL.")
    return match.group(1)


def _column_index_from_ref(header: list[str], column_name: str) -> int:
    value = (column_name or "").strip()
    if not value:
        raise ValueError("Column name is required.")

    normalized = value.lower()
    for idx, cell in enumerate(header):
        if str(cell).strip().lower() == normalized:
            return idx

    upper = value.upper()
    if upper.isalpha():
        idx = 0
        for ch in upper:
            idx = idx * 26 + (ord(ch) - ord("A") + 1)
        return idx - 1

    raise ValueError(f"Column '{value}' not found in the sheet header.")


def _build_csv_export_url(sheet_id: str, worksheet_gid: int) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={worksheet_gid}"


def _fetch_sheet_tab_locations(sheet_url: str, tab_name: str, column_name: str) -> tuple[list[str], str]:
    client = _sheet_client()
    sheet_id = _extract_sheet_id(sheet_url)
    book = client.open_by_key(sheet_id)
    ws = book.worksheet(tab_name)
    values = ws.get_all_values()
    header = values[0] if values else []
    if not header:
        return [], _build_csv_export_url(sheet_id, ws.id)

    column_idx = _column_index_from_ref(header, column_name)
    locations: list[str] = []
    seen: set[str] = set()
    for row in values[1:]:
        if column_idx >= len(row):
            continue
        text = str(row[column_idx]).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        locations.append(text)

    return locations, _build_csv_export_url(sheet_id, ws.id)


LOCATIONS = _resolve_locations(_OVERRIDES)
ALL_KEYWORD_CATEGORIES = _clean_keyword_categories(
    _OVERRIDES.get("keyword_categories"),
    _DEFAULT_KEYWORD_CATEGORIES,
)
KEYWORD_CATEGORY_1, KEYWORD_CATEGORY_2, KEYWORD_CATEGORY_3, KEYWORD_CATEGORY_4 = ALL_KEYWORD_CATEGORIES
TOTAL_CATEGORIES = len(ALL_KEYWORD_CATEGORIES)

# Legacy KEYWORDS for backward compatibility with parallel_scraper
KEYWORDS = KEYWORD_CATEGORY_1

# Number of unique leads (post-deduplication) to collect before pausing to
# request operator confirmation to continue. This is the total batch size for
# the run, not a per-worker multiplier.
BATCH_SIZE = _as_int(_OVERRIDES.get("batch_size"), 40, minimum=1)

# Where batch CSVs should be written (absolute path for consistent behavior).
OUTPUT_DIR = _SCRAPER_DIR / "output"

# Consolidated CSV containing every lead gathered during a session. Resets at
# the start of each run.
MASTER_OUTPUT = OUTPUT_DIR / "leads_full.csv"
TASKS_PROGRESS_FILE = OUTPUT_DIR / "completed_tasks.jsonl"
PENDING_BATCH_FILE = OUTPUT_DIR / "pending_batch.json"
RUN_LOCK_FILE = OUTPUT_DIR / "parallel_scraper.lock"

# Category state tracking file (tracks current category index)
CATEGORY_STATE_FILE = OUTPUT_DIR / "category_state.json"

# Progress tracking file to enable resuming after crashes/stops.
PROGRESS_FILE = OUTPUT_DIR / "scraper_progress.json"
SCRAPER_HEALTH_FILE = OUTPUT_DIR / "scraper_health.json"
SCRAPER_CALLOUTS_FILE = OUTPUT_DIR / "scraper_callouts.jsonl"
SCRAPER_STATS_FILE = OUTPUT_DIR / "scraper_stats.json"

# Webhook integration (Google Apps Script)
WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbzqD2ny083z43T6vnD2nifpYMI7RdbRXhOvrsFuhKHrJdlvF0Ealy7QwsDxJpVeSaYo/exec"
WEBHOOK_SECRET = "FMP-Scraper"
WEBHOOK_SPREADSHEET_ID = "1KrKoeun-h6eEzSK6-cc4_MUHriuabg8GyAR9b8NR7fc"
WEBHOOK_TAB_NAME = "Leads"

# Persistent Playwright profile directory so Google consent cookies survive
# across runs. Stored alongside the repo for transparency.
PLAYWRIGHT_USER_DATA = Path(".playwright-profile")

# Domains to drop because they belong to aggregators, directories, or social
# networks rather than to the business itself.
from common.config import AGGREGATOR_DOMAINS

# How long (seconds) we idle between Map item interactions. The throttling
# logic randomises between +/- this many seconds to appear human.
MIN_DELAY_S = 0.4
MAX_DELAY_S = 0.9

# UI interaction waits for expanding cards / loading sidebars.
CARD_CLICK_TIMEOUT_MS = 4000
SIDEBAR_AFTER_CLICK_WAIT_MS = 700
SCROLL_WAIT_MS = 900

# When Google presents a captcha we back off for this many seconds before
# surfacing the issue. Keeping it generous prevents lockouts.
CAPTCHA_BACKOFF_S = _as_int(_OVERRIDES.get("captcha_backoff_s"), 45, minimum=1)
CATEGORY_ADVANCE_TIMEOUT_MINUTES = _as_int(
    _OVERRIDES.get("category_advance_timeout_minutes"),
    30,
    minimum=1,
)

# ============================================================================
# PARALLEL SCRAPING CONFIGURATION
# ============================================================================
# Number of parallel browser instances. Set to 1 to disable parallel mode.
NUM_WORKERS = _as_int(_OVERRIDES.get("num_workers"), 4, minimum=1)

# Delay range (seconds) between requests. Actual delay is randomized within this range.
INTER_REQUEST_DELAY_MIN = _as_float(_OVERRIDES.get("inter_request_delay_min"), 6.0, minimum=0.0)
INTER_REQUEST_DELAY_MAX = max(
    INTER_REQUEST_DELAY_MIN,
    _as_float(_OVERRIDES.get("inter_request_delay_max"), 10.0, minimum=0.0),
)

# Stagger delay range (seconds) between starting each worker.
# Using a range for randomized stagger to appear more human-like.
WORKER_STAGGER_DELAY_MIN = _as_float(_OVERRIDES.get("worker_stagger_delay_min"), 3.0, minimum=0.0)
WORKER_STAGGER_DELAY_MAX = max(
    WORKER_STAGGER_DELAY_MIN,
    _as_float(_OVERRIDES.get("worker_stagger_delay_max"), 8.0, minimum=0.0),
)

# Validation and webhook hardening
WEBSITE_STATUS_WORKERS = _as_int(_OVERRIDES.get("website_status_workers"), 8, minimum=1)
WEBHOOK_MAX_RETRIES = 3
WEBHOOK_RETRY_BACKOFF_S = 3

# Batch-level guardrails
BATCH_WARNING_ERROR_RATE = _as_float(_OVERRIDES.get("batch_warning_error_rate"), 5.0, minimum=0.0)
BATCH_CRITICAL_ERROR_RATE = max(
    BATCH_WARNING_ERROR_RATE,
    _as_float(_OVERRIDES.get("batch_critical_error_rate"), 15.0, minimum=0.0),
)
BATCH_WARNING_WEBSITE_RATE = _as_float(_OVERRIDES.get("batch_warning_website_rate"), 45.0, minimum=0.0)
BATCH_NORMAL_WEBSITE_RATE = max(
    BATCH_WARNING_WEBSITE_RATE,
    _as_float(_OVERRIDES.get("batch_normal_website_rate"), 65.0, minimum=0.0),
)
BATCH_WARNING_YIELD = _as_float(_OVERRIDES.get("batch_warning_yield"), 80.0, minimum=0.0)
BATCH_NORMAL_YIELD = max(
    BATCH_WARNING_YIELD,
    _as_float(_OVERRIDES.get("batch_normal_yield"), 150.0, minimum=0.0),
)

# Worker shutdown tuning
WORKER_JOIN_TIMEOUT_S = 15
WORKER_TERMINATE_JOIN_TIMEOUT_S = 3
WORKER_KILL_JOIN_TIMEOUT_S = 2


def get_dashboard_config() -> dict:
    """Return scraper config in a dashboard-friendly shape."""
    overrides = _read_overrides()
    inline_locations = _clean_string_list(overrides.get("locations")) or _DEFAULT_LOCATIONS
    resolved_locations = _clean_string_list(overrides.get("location_resolved_locations"))
    location_source = _clean_location_source(overrides.get("location_source"))
    effective_locations = resolved_locations if location_source["mode"] == LOCATION_SOURCE_MODE_SHEET and resolved_locations else inline_locations
    keyword_categories = _clean_keyword_categories(
        overrides.get("keyword_categories"),
        _DEFAULT_KEYWORD_CATEGORIES,
    )
    return {
        "runtime": {
            "num_workers": _as_int(overrides.get("num_workers"), 4, minimum=1),
            "batch_size": _as_int(overrides.get("batch_size"), 40, minimum=1),
            "website_status_workers": _as_int(overrides.get("website_status_workers"), 8, minimum=1),
            "inter_request_delay_min": _as_float(overrides.get("inter_request_delay_min"), 6.0, minimum=0.0),
            "inter_request_delay_max": max(
                _as_float(overrides.get("inter_request_delay_min"), 6.0, minimum=0.0),
                _as_float(overrides.get("inter_request_delay_max"), 10.0, minimum=0.0),
            ),
            "worker_stagger_delay_min": _as_float(overrides.get("worker_stagger_delay_min"), 3.0, minimum=0.0),
            "worker_stagger_delay_max": max(
                _as_float(overrides.get("worker_stagger_delay_min"), 3.0, minimum=0.0),
                _as_float(overrides.get("worker_stagger_delay_max"), 8.0, minimum=0.0),
            ),
            "captcha_backoff_s": _as_int(overrides.get("captcha_backoff_s"), 45, minimum=1),
            "category_advance_timeout_minutes": _as_int(
                overrides.get("category_advance_timeout_minutes"),
                30,
                minimum=1,
            ),
        },
        "guardrails": {
            "batch_warning_error_rate": _as_float(overrides.get("batch_warning_error_rate"), 5.0, minimum=0.0),
            "batch_critical_error_rate": max(
                _as_float(overrides.get("batch_warning_error_rate"), 5.0, minimum=0.0),
                _as_float(overrides.get("batch_critical_error_rate"), 15.0, minimum=0.0),
            ),
            "batch_warning_website_rate": _as_float(overrides.get("batch_warning_website_rate"), 45.0, minimum=0.0),
            "batch_normal_website_rate": max(
                _as_float(overrides.get("batch_warning_website_rate"), 45.0, minimum=0.0),
                _as_float(overrides.get("batch_normal_website_rate"), 65.0, minimum=0.0),
            ),
            "batch_warning_yield": _as_float(overrides.get("batch_warning_yield"), 80.0, minimum=0.0),
            "batch_normal_yield": max(
                _as_float(overrides.get("batch_warning_yield"), 80.0, minimum=0.0),
                _as_float(overrides.get("batch_normal_yield"), 150.0, minimum=0.0),
            ),
        },
        "locations": inline_locations,
        "location_source": location_source,
        "location_resolved_locations": resolved_locations,
        "effective_locations": effective_locations,
        "keyword_categories": keyword_categories,
        "meta": {
            "locations_count": len(effective_locations),
            "inline_locations_count": len(inline_locations),
            "resolved_locations_count": len(resolved_locations),
            "keyword_counts": [len(group) for group in keyword_categories],
            "overrides_file": str(CONFIG_OVERRIDES_FILE),
        },
    }


def save_dashboard_config(payload: dict) -> dict:
    """Persist dashboard-managed scraper config overrides."""
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    guardrails = payload.get("guardrails") if isinstance(payload.get("guardrails"), dict) else {}
    current = _read_overrides()
    current_source = _clean_location_source(current.get("location_source"))
    incoming_source = payload.get("location_source") if isinstance(payload.get("location_source"), dict) else {}
    source = _clean_location_source({**current_source, **incoming_source})
    overrides = {
        "num_workers": _as_int(runtime.get("num_workers"), 4, minimum=1),
        "batch_size": _as_int(runtime.get("batch_size"), 40, minimum=1),
        "website_status_workers": _as_int(runtime.get("website_status_workers"), 8, minimum=1),
        "inter_request_delay_min": _as_float(runtime.get("inter_request_delay_min"), 6.0, minimum=0.0),
        "inter_request_delay_max": max(
            _as_float(runtime.get("inter_request_delay_min"), 6.0, minimum=0.0),
            _as_float(runtime.get("inter_request_delay_max"), 10.0, minimum=0.0),
        ),
        "worker_stagger_delay_min": _as_float(runtime.get("worker_stagger_delay_min"), 3.0, minimum=0.0),
        "worker_stagger_delay_max": max(
            _as_float(runtime.get("worker_stagger_delay_min"), 3.0, minimum=0.0),
            _as_float(runtime.get("worker_stagger_delay_max"), 8.0, minimum=0.0),
        ),
        "captcha_backoff_s": _as_int(runtime.get("captcha_backoff_s"), 45, minimum=1),
        "category_advance_timeout_minutes": _as_int(
            runtime.get("category_advance_timeout_minutes"),
            30,
            minimum=1,
        ),
        "batch_warning_error_rate": _as_float(guardrails.get("batch_warning_error_rate"), 5.0, minimum=0.0),
        "batch_critical_error_rate": max(
            _as_float(guardrails.get("batch_warning_error_rate"), 5.0, minimum=0.0),
            _as_float(guardrails.get("batch_critical_error_rate"), 15.0, minimum=0.0),
        ),
        "batch_warning_website_rate": _as_float(guardrails.get("batch_warning_website_rate"), 45.0, minimum=0.0),
        "batch_normal_website_rate": max(
            _as_float(guardrails.get("batch_warning_website_rate"), 45.0, minimum=0.0),
            _as_float(guardrails.get("batch_normal_website_rate"), 65.0, minimum=0.0),
        ),
        "batch_warning_yield": _as_float(guardrails.get("batch_warning_yield"), 80.0, minimum=0.0),
        "batch_normal_yield": max(
            _as_float(guardrails.get("batch_warning_yield"), 80.0, minimum=0.0),
            _as_float(guardrails.get("batch_normal_yield"), 150.0, minimum=0.0),
        ),
        "locations": _clean_string_list(payload.get("locations")) or _DEFAULT_LOCATIONS,
        "location_source": source,
        "location_resolved_locations": _clean_string_list(current.get("location_resolved_locations")),
        "keyword_categories": _clean_keyword_categories(
            payload.get("keyword_categories"),
            _DEFAULT_KEYWORD_CATEGORIES,
        ),
    }
    _write_overrides(overrides)
    return get_dashboard_config()


def save_location_sheet_sync(
    sheet_url: str,
    tab_name: str,
    column_name: str,
    resolved_locations: list[str],
    *,
    last_synced_at: str,
    csv_export_url: str = "",
) -> dict:
    """Persist synced sheet-backed locations into the scraper overrides."""
    current = _read_overrides()
    source = _clean_location_source(current.get("location_source"))
    source.update({
        "mode": LOCATION_SOURCE_MODE_SHEET,
        "sheet_url": str(sheet_url or "").strip(),
        "tab_name": str(tab_name or "").strip(),
        "column_name": str(column_name or "").strip(),
        "last_synced_at": str(last_synced_at or "").strip(),
        "csv_export_url": str(csv_export_url or "").strip(),
    })
    source = _clean_location_source(source)
    current.update({
        "location_source": source,
        "location_resolved_locations": _clean_string_list(resolved_locations),
    })
    _write_overrides(current)
    return get_dashboard_config()


def save_location_source_settings(source_payload: dict) -> dict:
    """Persist sheet-backed location source settings without syncing rows yet."""
    current = _read_overrides()
    source = _clean_location_source({
        **_clean_location_source(current.get("location_source")),
        **(source_payload if isinstance(source_payload, dict) else {}),
    })
    current["location_source"] = source
    _write_overrides(current)
    return get_dashboard_config()


def sync_location_source_now(*, advance: bool = False, respect_delay: bool = False) -> dict:
    """Sync the active sheet-backed location source into resolved locations."""
    current = _read_overrides()
    source = _clean_location_source(current.get("location_source"))
    if source["mode"] != LOCATION_SOURCE_MODE_SHEET:
        return get_dashboard_config()

    tab_names = source.get("tab_names") or ([source["tab_name"]] if source.get("tab_name") else [])
    if not tab_names:
        raise ValueError("No source tabs configured for sheet-backed locations.")

    active_index = min(source.get("active_tab_index", 0), len(tab_names) - 1)
    if advance and len(tab_names) > 1:
        active_index = (active_index + 1) % len(tab_names)
    elif respect_delay and source.get("auto_advance_enabled") and len(tab_names) > 1 and source.get("last_synced_at"):
        try:
            from datetime import datetime, timedelta
            last_synced = datetime.fromisoformat(source["last_synced_at"])
            if datetime.now() - last_synced >= timedelta(minutes=source.get("auto_advance_delay_minutes", 30)):
                active_index = (active_index + 1) % len(tab_names)
        except Exception:
            pass

    active_tab = tab_names[active_index]
    locations, csv_export_url = _fetch_sheet_tab_locations(
        source.get("sheet_url", ""),
        active_tab,
        source.get("column_name", ""),
    )

    source.update({
        "tab_names": tab_names,
        "tab_name": active_tab,
        "active_tab_index": active_index,
        "active_tab_name": active_tab,
        "last_synced_at": __import__("datetime").datetime.now().isoformat(),
        "csv_export_url": csv_export_url,
    })
    current.update({
        "location_source": _clean_location_source(source),
        "location_resolved_locations": _clean_string_list(locations),
    })
    _write_overrides(current)
    return get_dashboard_config()
