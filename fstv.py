from bs4 import BeautifulSoup
import asyncio
from playwright.async_api import async_playwright
import re

CHANNEL_MAPPINGS = {
    "usanetwork": {"name": "USA Network", "tv-id": "USA.Network.-.East.Feed.us"},
    "VE-usa-cbssport (sv3)": {"name": "CBS Sports", "tv-id": "CBS.Sports.Network.USA.us"},
    "VE-usa-cbs los angeles": {"name": "CBS Los Angeles", "tv-id": "CBS.(KCBS).Los.Angeles,.CA.us", "logo": "http://drewlive24.duckdns.org:9000/Logos/CBS.png"},
    "VE-usa-CBS GOLAZO CDN SV2": {"name": "CBS Sports Golazo!", "tv-id": "plex.tv.CBS.Sports.Golazo.Network.plex"},
    "VE-us-espn": {"name": "ESPN", "tv-id": "ESPN.us"},
    "VE-us-espn2": {"name": "ESPN2", "tv-id": "ESPN2.us"},
    "VE-usa-espn sec network": {"name": "SEC Network", "tv-id": "SEC.Network.us"},
    "VE-us-espnnews": {"name": "ESPNews", "tv-id": "ESPN.News.us"},
    "VE-cdn - us-uespn": {"name": "ESPNU", "tv-id": "ESPN.U.us"},
    "VE-us-espndeportes": {"name": "ESPN Deportes", "tv-id": "ESPN.Deportes.us"},
    "VE-usa-fs1 (sv2)": {"name": "FS1", "tv-id": "Fox.Sports.1.us"},
    "VE-usa-golf": {"name": "Golf Channel", "tv-id": "Golf.Channel.USA.us"},
    "VE-usa-fs2 (sv2)": {"name": "FS2", "tv-id": "Fox.Sports.2.us"},
    "VE-cdn - us-tennistv": {"name": "Tennis Channel", "tv-id": "The.Tennis.Channel.us"},
    "VE-cdn-us-tennistv2": {"name": "Tennis Channel 2", "tv-id": "The.Tennis.Channel.us"},
    "VE-us-nbc": {"name": "NBC", "tv-id": "NBC.(WNBC).New.York,.NY.us"},
    "VE-usa-cnbc": {"name": "CNBC", "tv-id": "CNBC.USA.us"},
    "VE-USa-UNIVERSO": {"name": "NBC Universo", "tv-id": "NBC.Universo.-.Eastern.feed.us"},
    "VE-us-msnbc": {"name": "MSNBC", "tv-id": "MSNBC.USA.us"},
    "ve-tnt1": {"name": "TNT Sports 1", "tv-id": "TNT.Sports.1.HD.uk"},
    "ve-tnt2": {"name": "TNT Sports 2", "tv-id": "TNT.Sports.2.HD.uk"},
    "ve-tnt3": {"name": "TNT Sports 3", "tv-id": "TNT.Sports.3.HD.uk"},
    "ve-tnt4": {"name": "TNT Sports 4", "tv-id": "TNT.Sports.4.HD.uk"},
    "VE-2uk-tntsport5 (sv3-CDN)": {"name": "TNT Sports 5", "tv-id": "TNT.Sports.Ultimate.uk"},
    "VE-SV3-UK-LALIGA": {"name": "La Liga UK", "tv-id": "LA.LIGA.za"},
    "VE-uk-skysportcricket": {"name": "Sky Sport Cricket UK", "tv-id": "SkySpCricket.HD.uk"},
    "VEsky sport main": {"name": "Sky Sport Main", "tv-id": "SkySpMainEvHD.uk"},
    "VE-1uk-skysportgolf": {"name": "Sky Sport Golf UK", "tv-id": "SkySp.Golf.HD.uk"},
    "VE-1uk-skysportnews": {"name": "Sky Sport News UK", "tv-id": "SkySp.News.HD.uk"},
    "VE-1UK - Sky Sport Mix (sv3-CDN)": {"name": "Sky Sport Mix UK", "tv-id": "SkySp.Mix.HD.uk"},
    "VE-uk-skysportarena": {"name": "Sky Sport Arena UK", "tv-id": "Sky.Sports+.Dummy.us"},
    "VE-uk-skysportf1": {"name": "Sky Sport F1 UK", "tv-id": "SkySp.F1.HD.uk"},
    "VEsky sport football": {"name": "Sky Sport Football", "tv-id": "SkySp.Fball.HD.uk"},
    "VE- uk - sky sport premier league": {"name": "Sky Sport Premier League UK", "tv-id": "SkyPremiereHD.uk"},
    "VEuk-sv2-sky sport plus (line2)": {"name": "Sky Sport Plus UK", "tv-id": "SkySp.PL.HD.uk"},
    "VE-1uk-skysporttennis": {"name": "Sky Sport Tennis UK", "tv-id": "SkySp.Tennis.HD.uk"},
    "VE-1cdn - uk-skysportracing": {"name": "Sky Sport Racing UK", "tv-id": "SkySp.Racing.HD.uk"},
    "VE-1cdn - uk-skysportaction": {"name": "Sky Sports Action UK", "tv-id": "SkySp.ActionHD.uk", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-kingdom/sky-sports-action-hz-uk.png?raw=true"},
    "VEuk-sky sport darts (sv2)": {"name": "Sky Sport Darts UK", "tv-id": "Sky.Sports+.Dummy.us"},
    "VE-3CDN - uk-eurosport1": {"name": "Eurosport 1 UK", "tv-id": "Eurosport.es"},
    "VE-3CDN - uk-eurosport2": {"name": "Eurosport 2 UK", "tv-id": "Eurosport.2.es"},
    "i24 news irs": {"name": "i24 News", "tv-id": "i24.News.us"},
    "channel 13 irs": {"name": "Channel 13 IRS", "tv-id": "Local.Programming.Dummy.us"},
    "VE-3uk-itv1 (sv2)": {"name": "ITV 1 UK", "tv-id": "ITV1.HD.uk"},
    "VE-3uk-itv2 (sv2)": {"name": "ITV 2 UK", "tv-id": "ITV2.HD.uk"},
    "VE-3uk-itv3 (sv2)": {"name": "ITV 3 UK", "tv-id": "ITV3.HD.uk"},
    "VE-3uk-itv4 (sv2)": {"name": "ITV 4 UK", "tv-id": "ITV4.HD.uk"},
    "VE-CDN - uk-lfctv": {"name": "LFC TV UK", "tv-id": "LFCTV.HD.uk"},
    "VE-CDN - uk-mutv": {"name": "MUTV UK", "tv-id": "MUTV.HD.uk"},
    "VE-uk-racingtv": {"name": "Racing TV UK", "tv-id": "Racing.TV.HD.uk"},
    "VE-3uk-premiersport1 (cdn)": {"name": "Premier Sport 1 UK", "tv-id": "Premier.Sports.1.HD.uk"},
    "VE-3uk-premiersport2 (cdn)": {"name": "Premier Sport 2 UK", "tv-id": "Premier.Sports.2.HD.uk", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-kingdom/premier-sports-2-uk.png?raw=true"},
    "VE-USA - Beinsport (sv3-CDN)": {"name": "BeIN Sports USA", "tv-id": "beIN.Sport.USA.us"},
    "VE-usa-Bein Espanol Xtra": {"name": "BeIN Sports Español Xtra", "tv-id": "613759"},
    "VE-usa-beinsport espanol": {"name": "BeIN Sports Español", "tv-id": "613758"},
    "VE-usa-beinsport xtra (sv3)": {"name": "BeIN Sports Xtra USA", "tv-id": "beIN.Sports.Xtra.(KSKJ-CD).Los.Angeles,.CA.us"},
    "VE-usa-fubosport (sv3)": {"name": "Fubo Sports USA", "tv-id": "Fubo.Sports.us", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/fubo-sports-network-us.png?raw=true"},
    "VE-ION USA": {"name": "ION USA", "tv-id": "ION..-.Eastern.Feed.us"},
    "VE-usa-foxsoccerplus": {"name": "Fox Soccer Plus", "tv-id": "FOX.Soccer.Plus.us"},
    "VE-usa-tycsport (sv3)": {"name": "TyC Sports", "tv-id": "TyC.Sports.us", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/argentina/tyc-sports-ar.png?raw=true"},
    "VE-usa-marquee sport network": {"name": "Marquee Sports Network", "tv-id": "Marquee.Sports.Network.us"},
    "VE-YES USA": {"name": "YES Network USA", "tv-id": "YES.Network.us"},
    "VE-usa-abcnews": {"name": "ABC News", "tv-id": "ABC.NEWS.us"},
    "VE-usa-bignetwork": {"name": "Big Network USA", "tv-id": "Big.Ten.Network.us", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/big-ten-network-us.png?raw=true"},
    "VE-usa-uni34": {"name": "Univision", "tv-id": "Univision.-.Eastern.Feed.us", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/univision-us.png?raw=true"},
    "VE-usa-ABC": {"name": "ABC", "tv-id": "ABC.(KABC).Los.Angeles,.CA.us"},
    "VE-usa-tudn (sv3)": {"name": "TUDN", "tv-id": "TUDN.us"},
    "VE-usa-fox channel": {"name": "Fox Los Angeles", "tv-id": "FOX.(KTTV).Los.Angeles,.CA.us", "logo": "http://drewlive24.duckdns.org:9000/Logos/FOX.png"},
    "VE-usa-telemundo": {"name": "Telemundo", "tv-id": "Telemundo.(KVEA).Los.Angeles,.CA.us"},
    "VE-usa-unimas": {"name": "UniMás", "tv-id": "UniMas.(KFTH).Houston,.TX.us", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/unimas-us.png?raw=true"},
    "VE-cdn -us-nhlnetwork": {"name": "NHL Network", "tv-id": "NHL.Network.USA.us", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/nhl-network-us.png?raw=true"},
    "VE-us-willowhd": {"name": "Willow Cricket HD", "tv-id": "Willow.Cricket.HDTV.(WILLOWHD).us"},
    "VE-us-willowxtra": {"name": "Willow Xtra", "tv-id": "Willow.Xtra.us", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/willow-xtra-us.png?raw=true"},
    "VE-cdn - us-nbatv": {"name": "NBA TV", "tv-id": "NBA.TV.USA.us", "logo": "http://drewlive24.duckdns.org:9000/Logos/NBATV.png"},
    "VE-cdn - us-nfl": {"name": "NFL Network", "tv-id": "NFL.Network.us"},
    "VE-us-mlbnetwork": {"name": "MLB Network", "tv-id": "MLB.Network.us"},
    "VE-us-cnn": {"name": "CNN", "tv-id": "CNN.us"},
    "VE-cdn - us-wnetwork": {"name": "W Network", "tv-id": "W.Network.Canada.East.(WTN).ca", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/canada/w-network-ca.png?raw=true"},
    "VE-cdn - us-accnetwork": {"name": "UniMás", "tv-id": "UniMas.(KFTH).Houston,.TX.us", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/unimas-us.png?raw=true"},
    "VE-us-foxnews": {"name": "Fox News", "tv-id": "Fox.News.us"},
    "VE-cdn - us-wfn": {"name": "World Fishing Network", "tv-id": "World.Fishing.Network.(US).(WFN).us", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/world-fishing-network-us.png?raw=true"},
    "VE-us-fightnetwork": {"name": "The Fight Network", "tv-id": "The.Fight.Network.(United.States).(TFN).us", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/fight-network-us.png?raw=true"},
    "VE-cdn - us-redzone": {"name": "NFL RedZone", "tv-id": "NFL.RedZone.us"},
    "ve-usa-trutv": {"name": "truTV", "tv-id": "truTV.USA.-.Eastern.us", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/tru-tv-us.png?raw=true"},
    "VE-TNT USA": {"name": "TNT", "tv-id": "TNT.-.Eastern.Feed.us"},
    "ve-fanduel sport": {"name": "FanDuel Sports Network", "tv-id": "FanDuel.Sports.Network.us", "logo": "http://drewlive24.duckdns.org:9000/Logos/FanDuelSportsNetwork.png"},
    "VE-usa-billiard tv": {"name": "Billiard TV", "tv-id": "plex.tv.Billiard.TV.plex"},
    "ve-ori-axstv": {"name": "AXS TV", "tv-id": "AXS.TV.USA.HD.us"},
    "VE-uk-bbcone (sv3)": {"name": "BBC One UK", "tv-id": "BBC.One.EastHD.uk"},
    "VE-uk-bbctwo": {"name": "BBC Two UK", "tv-id": "BBC.Two.HD.uk"},
    "VE-uk-bbcnews": {"name": "BBC News UK", "tv-id": "BBC.NEWS.HD.uk"},
    "VE-fox deportes": {"name": "Fox Deportes", "tv-id": "Fox.Deportes.us"},
    "VE-CA-One soccer": {"name": "OneSoccer Canada", "tv-id": "One.Soccer.ca", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/canada/one-soccer-ca.png?raw=true"},
    "VE-Paramount Network": {"name": "Paramount Network", "tv-id": "Paramount.Network.USA.-.Eastern.Feed.us", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/paramount-network-hz-us.png?raw=true"},
    "VE-uk-skycinemafamily": {"name": "Sky Cinema Family UK", "tv-id": "Local.Programming.us"},
    "VE-zeeuk-skycinemacomedy": {"name": "Sky Cinema Comedy UK", "tv-id": "Sky.Cinema.Comedy.it"},
    "VE-DE - DAZN 1 (sv3)": {"name": "DAZN 1 Germany", "tv-id": "DAZN.1.de", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/germany/dazn1-de.png?raw=true"},
    "VE-de-skyde top event": {"name": "Sky DE Top Event", "tv-id": "Sky.Sport.Top.Event.de"},
    "VE-DE - DAZN 2 (sv3)": {"name": "DAZN 2 Germany", "tv-id": "DAZN.2.de", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/germany/dazn2-de.png?raw=true"},
    "VE-de-skyde news": {"name": "Sky Sport News DE", "tv-id": "Sky.Sport.News.de"},
    "VE-de-sportdigital (sv3-CDN)": {"name": "SportDigital Germany", "tv-id": "sportdigital.Fussball.de"},
    "VE-de-sky premier league": {"name": "Sky Sport Premier League DE", "tv-id": "Sky.Sport.Premier.League.de"},
    "VE-de-skyde mix": {"name": "Sky Mix DE", "tv-id": "Sky.Sport.Mix.de", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-kingdom/sky-mix-uk.png?raw=true"},
    "VE-de-bundesliga1 (sv3-CDN)": {"name": "Bundesliga 1 Germany", "tv-id": "Sky.Sport.Bundesliga.de"},
    "VE-fox 502": {"name": "Fox Sports 502 AU", "tv-id": "FoxCricket.au"},
    "VE-zent-discovery": {"name": "Discovery Channel", "tv-id": "Discovery.Channel.(US).-.Eastern.Feed.us"},
    "VE-zent-cinemax": {"name": "Cinemax", "tv-id": "Cinemax.-.Eastern.Feed.us"},
    "VE-usa-hbo2": {"name": "HBO 2", "tv-id": "HBO.2.-.Eastern.Feed.us", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/hbo-2-us.png?raw=true"},
    "VE-zent-hbo": {"name": "HBO", "tv-id": "HBO.-.Eastern.Feed.us", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/hbo-us.png?raw=true"},
    "VE-TBS": {"name": "TBS", "tv-id": "TBS.-.East.us"},
    "VE-PT - Sporttv 1 (sv3)": {"name": "Sport TV1 Portugal", "tv-id": "SPORT.TV1.HD.pt", "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/portugal/sport-tv-1-pt.png?raw=true"},
    "VE-GOL TV": {"name": "GOL TV", "tv-id": "Gol.TV.USA.us"},
    "VE-TSN 1": {"name": "TSN 1", "tv-id": "TSN1.ca"},
    "VE-TSN 2": {"name": "TSN 2", "tv-id": "TSN2.ca"},
    "VE-TSN 3": {"name": "TSN 3", "tv-id": "TSN3.ca"},
    "VE-TSN 4": {"name": "TSN 4", "tv-id": "TSN4.ca"},
    "VE-TSN 5": {"name": "TSN 5", "tv-id": "TSN5.ca"},
}

def normalize_channel_name(name: str) -> str:
    return re.sub(r'\s+', ' ', name.strip().lower())

def prettify_name(raw: str) -> str:
    raw = re.sub(r'VE[-\s]*', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'\([^)]*\)', '', raw)  # remove things in ()
    raw = re.sub(r'[^a-zA-Z0-9\s]', '', raw)  # remove most non-alphanum
    return re.sub(r'\s+', ' ', raw.strip()).title()

async def fetch_fstv_html():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36")
        page = await context.new_page()

        print("🌐 Visiting FSTV...")

        for attempt in range(3):
            try:
                await page.goto("https://fstv.us/live-tv.html?timezone=America%2FDenver", timeout=90000, wait_until="domcontentloaded")
                await page.wait_for_selector(".item-channel", timeout=15000)
                break  # success
            except Exception as e:
                print(f"⚠️ Attempt {attempt + 1} failed: {e}")
                if attempt == 2:
                    raise

        html = await page.content()
        await browser.close()
        return html

def build_playlist_from_html(html, channel_mappings):
    soup = BeautifulSoup(html, "html.parser")
    channels = []

    for div in soup.find_all("div", class_="item-channel"):
        url = div.get("data-link")
        logo_html = div.get("data-logo")
        name = div.get("title")

        if not (url and name):
            continue

        normalized_name = normalize_channel_name(name)
        matched_key = None

        for raw_key in channel_mappings:
            if normalize_channel_name(raw_key) == normalized_name:
                matched_key = raw_key
                break

        if matched_key:
            mapping = channel_mappings[matched_key]
            new_name = mapping.get("name", prettify_name(name))
            tv_id = mapping.get("tv-id", "")
            logo = mapping.get("logo", logo_html)
        else:
            new_name = prettify_name(name)
            tv_id = ""
            logo = logo_html

        channels.append({
            "url": url,
            "logo": logo,
            "name": new_name,
            "tv_id": tv_id
        })

    playlist_lines = ['#EXTM3U\n']
    for ch in channels:
        tvg_id_attr = f' tvg-id="{ch["tv_id"]}"' if ch["tv_id"] else ""
        logo_attr = f' tvg-logo="{ch["logo"]}"' if ch["logo"] else ""
        playlist_lines.append(
            f'#EXTINF:-1{tvg_id_attr}{logo_attr} group-title="FSTV",{ch["name"]}\n'
        )
        playlist_lines.append(ch["url"] + "\n")

    return playlist_lines

async def main():
    try:
        html = await fetch_fstv_html()
        playlist_lines = build_playlist_from_html(html, CHANNEL_MAPPINGS)

        with open("FSTV24.m3u8", "w", encoding="utf-8") as f:
            f.writelines(playlist_lines)

        print(f"✅ Generated playlist with {len(playlist_lines)//2} channels in FSTV24.m3u8")
    except Exception as e:
        print(f"❌ Failed to generate playlist: {e}")

if __name__ == "__main__":
    asyncio.run(main())
