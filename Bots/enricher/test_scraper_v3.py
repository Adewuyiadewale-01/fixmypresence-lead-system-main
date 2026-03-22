import os
import sys
import unittest

from bs4 import BeautifulSoup

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from enricher.scraper_v3 import CandidatePool, LinkedInFinder, PersonExtractor, _same_site_family


FAM_STYLE_EMAIL_ONLY = """
<html lang="nl"><body>
<main>
  <section class="lawyers">
    <div class="lawyer-card">
      <h3>Lotte Castelijns</h3>
      <a href="mailto:castelijns@famadvocaten.nl">castelijns@famadvocaten.nl</a>
      <a href="/nl/lotte-castelijns-1/">Bekijk profiel</a>
    </div>
    <div class="lawyer-card">
      <h3>Dianne Kroezen</h3>
      <a href="mailto:kroezen@famadvocaten.nl">kroezen@famadvocaten.nl</a>
      <a href="/nl/dianne-kroezen/">Bekijk profiel</a>
    </div>
  </section>
</main>
</body></html>
"""

FAM_STYLE_MAIL_SCHEME = """
<html lang="nl"><body>
<main>
  <section class="lawyers">
    <div class="lawyer-card">
      <h3>Lotte Castelijns</h3>
      <a href="mail:castelijns@famadvocaten.nl">castelijns@famadvocaten.nl</a>
      <a href="/nl/lotte-castelijns-1/">Bekijk profiel</a>
    </div>
    <div class="lawyer-card">
      <h3>Dianne Kroezen</h3>
      <a href="mail:kroezen@famadvocaten.nl">kroezen@famadvocaten.nl</a>
      <a href="/nl/dianne-kroezen/">Bekijk profiel</a>
    </div>
  </section>
</main>
</body></html>
"""


SIGNAL_TEMPLATE_SWEEP = """
<html lang="nl"><body>
<main>
  <section class="team-grid">
    <div class="member-card">
      <h3>Joelle Lochtman</h3>
      <a href="mailto:lochtman@famadvocaten.nl">lochtman@famadvocaten.nl</a>
      <a href="/nl/joelle-lochtman/">Bekijk profiel</a>
    </div>
    <div class="member-card">
      <h3>Sofie van Vlijmen</h3>
      <a href="mailto:vanvlijmen@famadvocaten.nl">vanvlijmen@famadvocaten.nl</a>
      <a href="/nl/sofie-van-vlijmen/">Bekijk profiel</a>
    </div>
    <div class="member-card">
      <h3>Martijn Stammes</h3>
      <a href="/nl/martijn-stammes/">Bekijk profiel</a>
    </div>
  </section>
</main>
</body></html>
"""


ROLE_FIRST_TEMPLATE = """
<html lang="en"><body>
<main>
  <section class="leadership">
    <div class="leader-card">
      <h3>Alice Smith</h3>
      <p>Founder</p>
      <a href="https://linkedin.com/in/alice-smith">LinkedIn</a>
    </div>
    <div class="leader-card">
      <h3>Bob Jones</h3>
      <p>Founder</p>
    </div>
  </section>
</main>
</body></html>
"""


LINKEDIN_ONLY_SIGNAL = """
<html lang="en"><body>
<main>
  <section class="profiles">
    <div class="profile-card">
      <h3>Jaron Rijven</h3>
      <a href="https://www.linkedin.com/in/jaron-rijven">LinkedIn</a>
    </div>
  </section>
</main>
</body></html>
"""

COMBINED_NAME_ROLE_HEADINGS = """
<html lang="nl"><body>
<main>
  <section class="team-grid">
    <div class="member-card">
      <div class="image">img</div>
      <h3>Michiel Stokman - CEO</h3>
      <a href="https://www.linkedin.com/in/michielstokman/">LinkedIn</a>
    </div>
    <div class="member-card">
      <div class="image">img</div>
      <h3>Sean Martens - Hoofd product &amp; engineering</h3>
      <a href="https://www.linkedin.com/in/seanmartens/">LinkedIn</a>
    </div>
  </section>
</main>
</body></html>
"""

SECTION_TRUST_COMBINED_GRID = """
<html lang="nl"><body>
<main>
  <section class="team-grid">
    <div class="member-card">
      <div class="image">img</div>
      <h3>Michiel Stokman - CEO</h3>
      <a href="https://www.linkedin.com/in/michielstokman/">LinkedIn</a>
    </div>
    <div class="member-card">
      <div class="image">img</div>
      <h3>Colin Groos - COO</h3>
      <a href="https://www.linkedin.com/in/colingroos/">LinkedIn</a>
    </div>
    <div class="member-card">
      <div class="image">img</div>
      <h3>Anita Kwakman - Certificaathouders</h3>
      <a href="mailto:anita@bots.io">Email</a>
    </div>
  </section>
</main>
</body></html>
"""

SECTION_TRUST_ACROSS_ROWS = """
<html lang="nl"><body>
<main>
  <section class="team-grid">
    <div class="row">
      <div class="member-card">
        <div class="image">img</div>
        <h3>Michiel Stokman - CEO</h3>
        <a href="https://www.linkedin.com/in/michielstokman/">LinkedIn</a>
      </div>
      <div class="member-card">
        <div class="image">img</div>
        <h3>Colin Groos - COO</h3>
        <a href="https://www.linkedin.com/in/colingroos/">LinkedIn</a>
      </div>
    </div>
    <div class="row">
      <div class="member-card">
        <div class="image">img</div>
        <h3>Yuliia Valiieva - Financiën</h3>
        <a href="mailto:info@bots.io">Email</a>
      </div>
      <div class="member-card">
        <div class="image">img</div>
        <h3>Tom Bierwolf - Support</h3>
        <a href="mailto:info@bots.io">Email</a>
      </div>
    </div>
  </section>
</main>
</body></html>
"""

PHONE_NOT_ROLE = """
<html lang="nl"><body>
<main>
  <section class="lawyers">
    <div class="lawyer-card">
      <h3>Lotte Castelijns</h3>
      <a href="mail:castelijns@famadvocaten.nl">castelijns@famadvocaten.nl</a>
      <a href="tel:0202613770">020 261 3770</a>
      <a href="/nl/lotte-castelijns-1/">Bekijk profiel</a>
    </div>
  </section>
</main>
</body></html>
"""

NESTED_ADVOCAAT_ROLE = """
<html lang="nl"><body>
<main>
  <section class="profile">
    <div class="profile-card">
      <h1>Sofie van Vlijmen</h1>
      <div class="title"><span><strong>Advocaat &amp; Scheidingsmediator</strong></span></div>
      <a href="mail:vanvlijmen@famadvocaten.nl">vanvlijmen@famadvocaten.nl</a>
      <p>Specialist in familierecht en mediation met ruime ervaring.</p>
    </div>
  </section>
</main>
</body></html>
"""

FALSE_POSITIVE_NAMES = """
<html lang="nl"><body>
<main>
  <section class="cards">
    <div class="card">
      <h3>Fam. Advocaten</h3>
      <a href="mail:info@famadvocaten.nl">info@famadvocaten.nl</a>
      <a href="tel:0202613770">020 261 3770</a>
    </div>
    <div class="card">
      <h3>Privacy Statement</h3>
      <a href="/over-ons/">Over ons</a>
    </div>
  </section>
</main>
</body></html>
"""

AVADA_GRANDPARENT_CONSENSUS = """
<html lang="en"><body>
<main>
  <div class="entry-content-wrapper">
    <div class="flex_column av-dvlufgu-59dbe1c">
      <div class="content-block">
        <h3>Jaron Rijven</h3>
        <p>Founder</p>
        <a href="/jaron-rijven">Profile</a>
      </div>
    </div>
    <div class="flex_column av-13jdaby-61e1f09">
      <div class="content-block">
        <h3>Meyade Curfs</h3>
        <p>Founder</p>
        <a href="/meyade-curfs">Profile</a>
      </div>
    </div>
    <div class="flex_column av-5sxyr72-08ab882">
      <div class="content-block">
        <h3>Michael de Vos</h3>
        <p>Founder</p>
        <a href="/michael-de-vos">Profile</a>
      </div>
    </div>
  </div>
</main>
</body></html>
"""

SIGNAL_RICH_PAGE = """
<html lang="en"><body>
<main>
  <section class="team">
    <a href="https://linkedin.com/in/alice-smith">Alice</a>
    <a href="mailto:alice@company.nl">alice@company.nl</a>
    <a href="/people/alice-smith">Profile</a>
  </section>
</main>
</body></html>
"""

HEURISTIC_ONLY_PAGE = """
<html lang="en"><body>
<main>
  <h1>About Us</h1>
  <p>Learn more about our firm.</p>
</main>
</body></html>
"""

CONTACTLESS_UNIFORM_GRID = """
<html lang="en"><body>
<main>
  <section class="team-grid">
    <figure class="person-card">
      <img src="/a.jpg" />
      <div><div><h3>Hans Nibbering</h3><p>Co-Founder &amp; Managing Partner</p></div></div>
    </figure>
    <figure class="person-card">
      <img src="/b.jpg" />
      <div><div><h3>Kai Yuen</h3><p>Managing Director</p></div></div>
    </figure>
    <figure class="person-card">
      <img src="/c.jpg" />
      <div><div><h3>Maarten Zant</h3><p>Co-Founder &amp; Advisor to the Board</p></div></div>
    </figure>
  </section>
</main>
</body></html>
"""

PLAINTEXT_EMAIL_ROLE_CARDS = """
<html lang="en"><body>
<main>
  <section class="leadership">
    <div class="leader-card">
      <img src="/alice.jpg" />
      <h3>Alice Smith</h3>
      <p>Founder</p>
      <span>alice.smith@example.com</span>
    </div>
    <div class="leader-card">
      <img src="/bob.jpg" />
      <h3>Bob Jones</h3>
      <p>Founder</p>
      <span>bob.jones@example.com</span>
    </div>
  </section>
</main>
</body></html>
"""


class TestPersonExtractorV3(unittest.TestCase):
    def setUp(self):
        self.extractor = PersonExtractor()

    def _extract(self, html: str):
        soup = BeautifulSoup(html, "html.parser")
        return self.extractor.extract_from_page_artifact_v2(soup, "https://example.com/page")

    def test_surname_only_email_matches_name(self):
        persons = self._extract(FAM_STYLE_EMAIL_ONLY)
        names = {p["name"] for p in persons}
        self.assertIn("Lotte Castelijns", names)
        self.assertIn("Dianne Kroezen", names)

    def test_mail_scheme_matches_name(self):
        persons = self._extract(FAM_STYLE_MAIL_SCHEME)
        names = {p["name"] for p in persons}
        emails = {p["email"] for p in persons}
        self.assertIn("Lotte Castelijns", names)
        self.assertIn("Dianne Kroezen", names)
        self.assertIn("castelijns@famadvocaten.nl", emails)
        self.assertIn("kroezen@famadvocaten.nl", emails)

    def test_signal_template_sweeps_contactless_sibling(self):
        persons = self._extract(SIGNAL_TEMPLATE_SWEEP)
        names = {p["name"] for p in persons}
        self.assertEqual(names, {"Joelle Lochtman", "Sofie van Vlijmen", "Martijn Stammes"})

    def test_role_first_template_sweeps_peer(self):
        persons = self._extract(ROLE_FIRST_TEMPLATE)
        names = {p["name"] for p in persons}
        self.assertIn("Alice Smith", names)
        self.assertIn("Bob Jones", names)

    def test_linkedin_only_signal_extracts_person(self):
        persons = self._extract(LINKEDIN_ONLY_SIGNAL)
        self.assertEqual(len(persons), 1)
        self.assertEqual(persons[0]["name"], "Jaron Rijven")

    def test_combined_name_role_heading_extracts_name_and_role(self):
        persons = self._extract(COMBINED_NAME_ROLE_HEADINGS)
        by_name = {p["name"]: p for p in persons}
        self.assertIn("Michiel Stokman", by_name)
        self.assertIn("Sean Martens", by_name)
        self.assertEqual(by_name["Michiel Stokman"]["role"], "CEO")
        self.assertEqual(by_name["Sean Martens"]["role"], "Hoofd product & engineering")

    def test_section_trust_sweeps_unfamiliar_combined_role(self):
        persons = self._extract(SECTION_TRUST_COMBINED_GRID)
        by_name = {p["name"]: p for p in persons}
        self.assertIn("Anita Kwakman", by_name)
        self.assertEqual(by_name["Anita Kwakman"]["role"], "Certificaathouders")

    def test_section_trust_can_cross_row_wrappers(self):
        persons = self._extract(SECTION_TRUST_ACROSS_ROWS)
        names = {p["name"] for p in persons}
        self.assertIn("Yuliia Valiieva", names)
        self.assertIn("Tom Bierwolf", names)

    def test_phone_does_not_fill_role(self):
        persons = self._extract(PHONE_NOT_ROLE)
        self.assertEqual(len(persons), 1)
        self.assertEqual(persons[0]["name"], "Lotte Castelijns")
        self.assertEqual(persons[0]["role"], "")

    def test_nested_advocaat_role_is_captured(self):
        persons = self._extract(NESTED_ADVOCAAT_ROLE)
        self.assertEqual(len(persons), 1)
        self.assertEqual(persons[0]["role"], "Advocaat & Scheidingsmediator")

    def test_org_and_legal_labels_are_not_people(self):
        persons = self._extract(FALSE_POSITIVE_NAMES)
        self.assertEqual(persons, [])

    def test_shared_grandparent_consensus_promotes_hashed_cards(self):
        persons = self._extract(AVADA_GRANDPARENT_CONSENSUS)
        names = {p["name"] for p in persons}
        self.assertEqual(names, {"Jaron Rijven", "Meyade Curfs", "Michael de Vos"})

    def test_signal_rich_artifact_ranks_first(self):
        finder = LinkedInFinder()
        strong = finder._make_page_artifact(
            "https://example.com/people",
            BeautifulSoup(SIGNAL_RICH_PAGE, "html.parser"),
            {"https://linkedin.com/in/alice-smith"},
            {"alice@company.nl"},
        )
        weak = finder._make_page_artifact(
            "https://example.com/about",
            BeautifulSoup(HEURISTIC_ONLY_PAGE, "html.parser"),
            set(),
            set(),
        )
        ordered = sorted([weak, strong], key=finder._artifact_priority_key, reverse=True)
        self.assertEqual(ordered[0].url, "https://example.com/people")

    def test_contactless_uniform_grid_is_promoted(self):
        persons = self._extract(CONTACTLESS_UNIFORM_GRID)
        names = {p["name"] for p in persons}
        self.assertEqual(names, {"Hans Nibbering", "Kai Yuen", "Maarten Zant"})
        by_name = {p["name"]: p for p in persons}
        self.assertEqual(by_name["Hans Nibbering"]["strategy"], "contactless_grid")

    def test_plaintext_email_supports_role_confirmed_cards(self):
        persons = self._extract(PLAINTEXT_EMAIL_ROLE_CARDS)
        by_name = {p["name"]: p for p in persons}
        self.assertIn("Alice Smith", by_name)
        self.assertIn("Bob Jones", by_name)
        self.assertEqual(by_name["Alice Smith"]["email"], "alice.smith@example.com")
        self.assertEqual(by_name["Bob Jones"]["email"], "bob.jones@example.com")

    def test_same_site_family_treats_locale_subdomain_as_internal(self):
        self.assertTrue(_same_site_family("www.bots.io", "nl.bots.io"))
        self.assertTrue(_same_site_family("nl.bots.io", "bots.io"))

    def test_candidate_pool_accepts_locale_subdomain_redirects(self):
        pool = CandidatePool("http://www.bots.io")
        pool.add("https://nl.bots.io/ons-verhaal", "homepage_anchor")
        ranked = pool.ranked(10)
        self.assertIn("https://nl.bots.io/ons-verhaal", ranked)


if __name__ == "__main__":
    unittest.main()
