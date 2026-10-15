# Constitutions sweep: the Americas, Europe, the Caucasus and Central Asia

Read 2026-10-15 by `scripts/build_constitution_findings_b.py` from `scripts/constitution_findings_b_data.py`; the sources are
`config/sources/<id>.yaml` (the full robots.txt, terms and document findings of each publisher). Research aid, not legal advice.

## Method

- Scope: every country of the Americas, of Europe (including the microstates and Kosovo) and of the Caucasus and Central Asia that had no active
  legislation source in `docs/corpus.md`; the constitution only. The 3 ingested documents' source is one descriptor per publisher.
- Per host, the robots.txt was read first and every path checked against it (a group naming `ulis-research-bot`, else `User-agent: *`;
  longest match). A host that answered 401/403/405 on robots.txt, a path it disallows, a TLS certificate chain that could not be verified, a bot
  challenge or a CAPTCHA ended the attempt for that host: no other agent, proxy, mirror or browser was used.
- At most about six requests per country: robots.txt, the page or PDF holding the constitution, and the terms or footer pages. Web search was
  used only to find addresses (it does not touch the publishers' hosts).
- A source is ingested only when the publisher's own words allow reuse (CC BY-SA, a portal notice) or a statute that excludes legislative texts
  from copyright was read and quoted. A bare "© ... all rights reserved" footer, no terms at all, a bot challenge or a refusal leaves it `planned`; terms that
  forbid the use make it `rejected`.
- Connector and parser: `constitution_pages_b` (HTML, text-layer PDF or text; articles split by the document's own headings; blocks of
  paragraphs where the structure is not recognisable); citation `<Country> Constitution art. N` (module `constitution_b`).

## Result

- Ingested (status `active`): 3 publishers, 4 documents: Costa Rica (es), Mexico (es), Belarus (ru, be).
- Recorded as `planned`: 62 publishers; as `rejected`: 2 (Dominica, Portugal's Tribunal Constitucional).
- Not reachable at all from the sandbox (refusal, unreachable host, unverifiable TLS chain): see the rows whose finding says so.

## Outcomes by country

### Americas

| Country | Source | Source URL | Language | Finding | Outcome |
|---|---|---|---|---|---|
| Antigua and Barbuda | `ag-laws-constitution` | https://laws.gov.ag/wp-content/uploads/2018/08/cap-23.pdf | en | No reuse terms found - footer copyright notice only. Home page footer: "©2026 - laws.gov.ag Address: Government Complex, St. John's, Antigua"; no terms of use or licence found. | planned |
| Barbados | `bb-gis-constitution` | https://gisbarbados.gov.bb/download/the-constitution-of-barbados/ | en | Terms not read - robots.txt refused (HTTP 403). Not read for the GIS host (refused). The Parliament's pages carry no terms that were found. | planned |
| Belize | `bz-belizelaw-constitution` | https://www.belizelaw.org/ | en | Not read - host unreachable or suspended | planned |
| Bolivia | `bo-oep-constitution` | https://web.oep.org.bo/wp-content/uploads/2016/12/constitucion_politica_estado.pdf | es | No reuse terms found. No terms page was found from the home page or the footer; none was read. (the Gaceta Oficial host, www.gacetaoficialdebolivia.gob.bo, ends the TLS handshake early: UNEXPECTED_EOF; www.tcpbolivia.bo presents a certificate for another host name.) | planned |
| Brazil | `br-senado-constitution` | https://www2.senado.leg.br/bdsf/bitstream/handle/id/645769/CF88_EC132_livro.pdf | pt | Terms not read - security check page in front of the file | planned |
| Chile | `cl-camara-constitution` | https://www.camara.cl/camara/doc/leyes_normas/constitucion.pdf | es | Document refused (HTTP 403); content-signal policy in robots.txt | planned |
| Colombia | `co-senado-constitution` | https://www.senado.gov.co/images/Archivospdf/elsenado/Normatividad/constitucion_politica.pdf | es | No reuse terms found. No terms page was found from the home page or the footer; none was read. | planned |
| Costa Rica | `cr-tse-constitution` | https://www.tse.go.cr/pdf/normativa/constitucion.pdf | es | Site footer: "Basada en una obra en https://www.tse.go.cr" with the licence link http://creativecommons.org/licenses/by-sa/4.0/ (rel="license"); the Términos y Condiciones de Uso restrict only the Tribunal's marks and graphics. | ingested - CC BY-SA 4.0 (AMBER: footer licence) |
| Cuba | `cu-gaceta-constitution` | https://www.gacetaoficial.gob.cu/sites/default/files/goc-2019-ex5.pdf | es | Not read - host unreachable | planned |
| Dominica | `dm-government-constitution` | https://dominica.gov.dm/privacy-copyright-notices | en | Terms of the portal: personal and non-commercial use only; no distribution. "Privacy & Copyright Notices": "You may download, store in cache, display and copy material published on this web site only in their unaltered form and solely for your personal and non-commercial use ... You must not transmit or distribute any part of this web site without prior written permission of the Government of the Commonwealth of ... | rejected |
| Dominican Republic | `do-presidencia-constitution` | https://presidencia.gob.do/sites/default/files/statics/transparencia/base-legal/Constitucion-de-la-Republica-Dominicana-2015-actualizada.pdf | es | No reuse terms found. No terms page was found from the home page or the footer; none was read. (Cámara de Diputados footer: "Términos de uso / Políticas de privacidad ... © 2024 Todos los derechos reservados"; Tribunal Constitucional footer: "© 2026 , Todos los Derechos Reservados".) | planned |
| Ecuador | `ec-asamblea-constitution` | https://www.asambleanacional.gob.ec/sites/default/files/documents/old/constitucion_de_bolsillo.pdf | es | No reuse terms found. No terms page was found from the home page or the footer; none was read. | planned |
| El Salvador | `sv-asamblea-constitution` | https://www.asamblea.gob.sv/ | es | Not read - TLS chain incomplete | planned |
| Grenada | `gd-government-constitution` | https://www.gov.gd/government/the-constitution | en | No reuse terms found - footer copyright notice only. Footer: "© 2026 Government of Grenada"; no terms of use or licence found. | planned |
| Guatemala | `gt-congreso-constitution` | https://www.congreso.gob.gt/contenido/20 | es | Document not reachable without scripts; one host refused (HTTP 403). No terms page was found from the home page or the footer; none was read. | planned |
| Guyana | `gy-parliament-constitution` | https://parliament.gov.gy/Constitution%20of%20the%20Cooperatiive%20Republic%20of%20Guyana.pdf | en | No reuse terms found - copyright notice only. Home page footer: "Copyright Information Disclaimer Accessibility Information Copyright © 2014 - 2026 Parliament of the Co-operative Republic of Guyana"; the "Copyright Information" link points back to the home page and no copyright text was found. | planned |
| Haiti | `ht-primature-constitution` | https://www.primature.gouv.ht/ | fr | Document not found; no terms found. No terms page was found from the home page or the footer; none was read. | planned |
| Honduras | `hn-tsc-constitution` | https://www.tsc.gob.hn/web/leyes/Constitucion_de_la_republica.pdf | es | No reuse terms found - footer copyright notice only. Home page footer: "© Copyright 2023 Tribunal Superior de Cuentas - TSC / Desarrollado por G O Consultores S de R. L."; no terms of use or licence found. | planned |
| Jamaica | `jm-laws-constitution` | https://laws.moj.gov.jm/ | en | Not read - TLS chain incomplete on the laws host. Parliament home page footer: "Copyright (c) 2008 Jamaica Houses of Parliament Designed by the Jamaica Information Service (JIS)". | planned |
| Mexico | `mx-diputados-constitution` | https://www.diputados.gob.mx/LeyesBiblio/pdf/CPEUM.pdf | es | No robots.txt, no terms. Ley Federal del Derecho de Autor art. 14: "No son objeto de la protección como derecho de autor ... VIII. Los textos legislativos, reglamentarios, administrativos o judiciales, así como sus traducciones oficiales" (read on the same host). | ingested - statutory basis (AMBER) |
| Nicaragua | `ni-asamblea-constitution` | https://www.asamblea.gob.ni/assets/constitucion.pdf | es | No text layer in the PDF; no terms found. No terms page was found from the home page or the footer; none was read. | planned |
| Panama | `pa-organo-judicial-constitution` | https://www.organojudicial.gob.pa/uploads/blogs.dir/2/2024/11/665/constitucion-politica-de-la-republica-de-panama.pdf | es | "Todos los derechos reservados" - no reuse terms found. Home page footer: "© 2023 Órgano Judicial . Todos los derechos reservados."; no terms of use found. | planned |
| Paraguay | `py-senado-constitution` | https://www.senado.gov.py/ | es | Document not found at the known addresses; no terms found. No terms page was found from the home page or the footer; none was read. | planned |
| Peru | `pe-congreso-constitution` | https://www3.congreso.gob.pe/Docs/constitucion/constitucion/Constitucion-web_2026.pdf | es | No reuse terms found. No terms page was found from the home page or the footer; none was read. | planned |
| Saint Kitts and Nevis | `kn-government-constitution` | https://www.gov.kn/ | en | Document not found; no terms found. No terms page was found from the home page or the footer; none was read. | planned |
| Saint Lucia | `lc-government-constitution` | https://www.govt.lc/media.govt.lc/www/resources/legislation/ConstitutionOfSaintLucia.pdf | en | No reuse terms found. No terms page was found from the home page or the footer; none was read. | planned |
| Saint Vincent and the Grenadines | `vc-government-constitution` | https://www.gov.vc/ | en | Document not found; no terms found. Footer: "© 2024 Government of Saint Vincent and the Grenadines / website by ITSD"; no terms found. | planned |
| Suriname | `sr-dna-constitution` | https://www.dna.sr/wetgeving/surinaamse-wetten/ | nl | Copyright reserved; parts of the content may be reproduced naming the source (no constitution text found). https://www.dna.sr/copyright/auteursrechten/: "Het auteursrecht op de website wordt uitdrukkelijk voorbehouden door De Nationale Assemblee. Onder voorwaarde dat (een link naar) de bron wordt vermeld, mogen onderdelen van de inhoud van dna.sr worden overgenomen." | planned |
| The Bahamas | `bs-laws-constitution` | https://laws.bahamas.gov.bs/ | en | Terms not read - access refused (HTTP 403). Not read - access refused. | planned |
| Trinidad and Tobago | `tt-laws-constitution` | https://laws.gov.tt/pdf/Constitution.pdf | en | No reuse terms found for laws.gov.tt. The Parliament's footer links "Privacy Policy" and "Terms of Use" (not read) and states "© Copyright Parliament of the Republic of Trinidad and Tobago 2025"; no terms for laws.gov.tt found. | planned |
| Venezuela | `ve-asamblea-constitution` | https://www.asambleanacional.gob.ve/storage/documentos/botones/constitucion-nacional-20191205135853.PDF | es | No reuse terms found. No terms page was found from the home page or the footer; none was read. | planned |

### Europe

| Country | Source | Source URL | Language | Finding | Outcome |
|---|---|---|---|---|---|
| Albania | `al-qbz-constitution` | https://qbz.gov.al/ | sq | Terms not read - script application, no static text | planned |
| Andorra | `ad-consell-general-constitution` | https://www.consellgeneral.ad/ca/el-consell-dandorra/constitucio-i-reglament/la-constitucio-del-principat-d-andorra | ca | Not fetched - no terms found. No terms page was found from the home page or the footer; none was read. The Butlletí Oficial (www.bopa.ad) is a script application. | planned |
| Austria | - | https://www.vfgh.gv.at/ ; https://www.parlament.gv.at/verfassung/ | de | https://www.vfgh.gv.at/robots.txt: "User-agent: * Disallow:" (nothing); the Court's pages read hold no text of the B-VG. https://www.parlament.gv.at/robots.txt is readable; the guessed address /verfassung/ answered HTTP 404. The B-VG is on the RIS, whose source at-ris-bundesrecht stays planned (data host disallows all robots). | not found elsewhere - no new descriptor; see at-ris-bundesrecht |
| Belarus | `by-president-constitution` | https://president.gov.by/ru/gosudarstvo/constitution | ru, be | "Материалы Портала могут использоваться ... без каких-либо ограничений по объему и срокам публикации. Единственным условием является ссылка на первоисточник. Никакого предварительного согласия ... не требуется." | ingested - portal notice (AMBER) |
| Belgium | `be-dekamer-constitution` | https://www.dekamer.be/kvvcr/pdf_sections/publications/constitution/GrondwetFR.pdf | fr, nl | Chamber FAQ: official acts may be reproduced freely, Chamber publications not without permission - the PDF is a Chamber publication (undecided). The Chamber's FAQ (showpage.cfm ... faq.cfm?goto=doccopy): "Overeenkomstig artikel XI.172, § 2, van het Wetboek Economisch Recht bestaat er geen auteursrecht op de officiële akten van de overheid. Parlementaire documenten die officiële akten uitmaken, mogen dus vrij ... | planned |
| Belgium | `be-senate-constitution` | https://www.senate.be/doc/const_fr.html | fr, nl | No reuse terms found - copyright notice only. Page footer: "Copyright © Belgian Senate Page last updated on 20/06/2025 Contact the Webmaster"; no terms of use found. (The Chamber's FAQ quotes art. XI.172 § 2 of the Code de droit économique: no copyright in official acts of the authorities.) | planned |
| Bosnia and Herzegovina | `ba-ustavni-sud-constitution` | https://www.ustavnisud.ba/bs/ustav-bosne-i-hercegovine | bs | "Sva prava zadržana" - no reuse terms found. Court footer: "Copyrights @ 2026 Ustavni sud BiH Sva prava zadržana." (all rights reserved); no terms of use found. | planned |
| Bulgaria | `bg-parliament-constitution` | https://www.parliament.bg/bg/const | bg | Terms not read - script application, no static text | planned |
| Cyprus | `cy-parliament-constitution` | https://www.parliament.cy/el/links-in-home-and-footer/significant-papers/cyprus-independence/constitution-1960 | el | Document path disallowed by robots.txt | planned |
| Greece | `gr-hellenic-parliament-constitution` | https://www.hellenicparliament.gr/Vouli-ton-Ellinon/To-Politevma/Syntagma/ | el | Terms not read - access refused (HTTP 403). Not read - access refused. | planned |
| Hungary | `hu-magyar-kozlony-constitution` | https://magyarkozlony.hu/dokumentumok/b1a8d0225dae97eeef555d20f85644cfdba413da/letoltes | hu | No reuse terms found - copyright notice only. Home page footer: "2026 © Miniszterelnökség Impresszum Kapcsolat Jogszabályi háttér"; no terms of use found. | planned |
| Iceland | `is-althingi-constitution` | https://www.althingi.is/lagas/nuna/1944033.html | is | Terms not read - access refused (HTTP 403). Not read - access refused. | planned |
| Kosovo | `xk-kryeministri-constitution` | https://kryeministri.rks-gov.net/wp-content/uploads/2022/03/Kushtetuta.e.Republikes.se_.Kosoves.pdf | sq | No reuse terms found. No terms page was found from the home page or the footer; none was read. | planned |
| Lithuania | `lt-seimas-constitution` | https://www.lrs.lt/home/Konstitucija/Konstitucija.htm | lt | No reuse terms found - copyright notice only. Home page footer: "© Lietuvos Respublikos Seimo kanceliarija, biudžetinė įstaiga"; no terms of use found. | planned |
| Malta | `mt-parliament-constitution` | https://parlament.mt/ | en, mt | Terms not read - access refused (HTTP 403) | planned |
| Moldova | `md-presedinte-constitution` | https://presedinte.md/app/webroot/Constitutia_RM/Constitutia_2024_ro.pdf | ro | No reuse terms found. Constitutional Court footer: "© 2026 Curtea Constituțională a Republicii Moldova. Toate drepturile rezervate."; no terms found on presedinte.md. | planned |
| Monaco | `mc-legimonaco-constitution` | https://legimonaco.mc/constitution | fr | Terms not read. Not read (the home page carries a footer menu; no terms page was followed). | planned |
| Montenegro | `me-gov-constitution` | https://www.gov.me/dokumenta/8001ff01-60bd-451a-be3f-83e7b71e112a | cnr | "Sva prava zadržana" on the court's site - no reuse terms found. Constitutional Court footer: "© 2026 Ustavni sud Crne Gore. Sva prava zadržana."; no terms found on gov.me. | planned |
| North Macedonia | `mk-sobranie-constitution` | https://www.sobranie.mk/ustav-na-rm.nspx | mk | No reuse terms found. No terms page was found from the home page or the footer; none was read. | planned |
| Norway | `no-stortinget-constitution` | https://www.stortinget.no/globalassets/pdf/diverse/grunnloven.pdf | nb, nn | No general reuse terms found; two-column PDF of the 2012 text. No general terms for stortinget.no content were found (the teaching site states copyright in its own illustrations; the art gallery and web-TV have separate terms). | planned |
| Portugal | `pt-parlamento-constitution` | https://www.parlamento.pt/Legislacao/Paginas/ConstituicaoRepublicaPortuguesa.aspx | pt | Terms not read. Home page footer menu: "Propriedade e privacidade", "Cookies", "© Assembleia da República"; the page behind it was not found at the guessed address (HTTP 404) and no terms text was read. | planned |
| Portugal | `pt-tribunal-constitucional-constitution` | https://www.tribunalconstitucional.pt/tc/crp.html | pt | Terms: non-profit use with source; copying and dissemination of site texts outside Jurisprudência expressly forbidden. https://www.tribunalconstitucional.pt/tc/infolegal.html: "O utilizador pode copiar, importar ou utilizar gratuitamente informações existentes neste sitio web, para uso pessoal ou público, desde que não tenha finalidades lucrativas, devendo, contudo, referir a fonte de informação. Excetuando os ... | rejected |
| Romania | `ro-cdep-constitution` | https://www.cdep.ro/pls/dic/site.page?id=339 | ro | Not read - hosts unreachable | planned |
| Russia | `ru-kremlin-constitution` | https://kremlin.ru/acts/constitution | ru | Not read - hosts unreachable | planned |
| San Marino | `sm-consiglio-grande-constitution` | https://www.consigliograndeegenerale.sm/ | it | Terms not read - access refused (HTTP 403). Not read - access refused. | planned |
| Serbia | `rs-srbija-constitution` | https://www.srbija.gov.rs/dokument/330350/ustav-republike-srbije.php | sr | Not read - TLS chain incomplete on the Court's host | planned |
| Slovakia | - | https://www.nrsr.sk/web/ | sk | https://www.nrsr.sk/robots.txt: name or service not known (the host did not resolve). Slovakia has the suspended source sk-slovlex-static. | unreachable - no new descriptor |
| Türkiye | `tr-tbmm-constitution` | https://cdn.tbmm.gov.tr/TbmmWeb/Anayasa/anayasa_2018.pdf | tr | No reuse terms found; robots.txt has rules aimed at AI crawlers. No terms of use found on the pages read (cookie policy only). | planned |
| Ukraine | `ua-constitutional-court-constitution` | https://ccu.gov.ua/sites/default/files/commemorative_constitution_2021.pdf | uk | No reuse terms found; the edition interleaves decision excerpts and is dated 2021. Court footer: "© 2026 Конституційний Суд України"; no terms of use found. | planned |
| Vatican City State | `va-governatorato-constitution` | https://www.vaticanstate.va/phocadownload/leggi-decreti/normativa-generale/Legge%20fondamentale%20dello%20S.C.V.%2013%20maggio%2023.pdf | it | "Tutti i diritti riservati" - no reuse terms found. Home page footer: "Copyright © 2026 by “Governatorato dello Stato della Città del Vaticano" / Tutti i diritti riservati". | planned |

### Caucasus and Central Asia

| Country | Source | Source URL | Language | Finding | Outcome |
|---|---|---|---|---|---|
| Armenia | `am-constitutional-court-constitution` | https://www.concourt.am/en/normative-legal-bases/constitution-of-ra/sahmanadrowt-yown-2015-t-vakani-dektemberi-6-i-hanrak-veov-endownvats-p-op-okhowt-yownnerov | en, hy | Copyright protected - no reuse terms found. Court footer: "© 2021-2026, Հեղինակային իրավունքները պաշտպանված են" (copyright protected); no terms of use found. The legal information system arlis.am is recorded separately (am-arlis). | planned |
| Azerbaijan | `az-president-constitution` | https://president.az/en/pages/view/azerbaijan/constitution | az, en | "All rights reserved" - no reuse terms found. Page footer: "© 2010-2026 The Official Website of the President of the Republic of Azerbaijan. All rights reserved. This website is managed by ..."; no terms of use found. | planned |
| Georgia | `ge-constitutional-court-constitution` | https://www.constcourt.ge/en/court/legislation/constitution-text | en, ka | "All rights reserved" footer - no reuse terms found. Page footer: "ALL RIGHTS RESERVED MADE BY IDEADESIGNGROUP"; no terms of use found. The legislative herald (matsne.gov.ge, source ge-matsne) has user categories and fees. | planned |
| Kazakhstan | `kz-akorda-constitution` | https://akorda.kz/ru/official_documents/constitution | ru, kk | No reuse terms found. No terms of use found on the pages read. | planned |
| Kyrgyzstan | `kg-president-constitution` | https://president.kg/ | ru, ky | Not read - host unreachable | planned |
| Tajikistan | `tj-president-constitution` | https://www.president.tj/ | ru, tg | Terms not read - script application, no static text | planned |
| Turkmenistan | `tm-minjust-constitution` | https://minjust.gov.tm/assets/files/law_documents/hukuknama_1_tm.pdf | tk | No reuse terms found. No terms page was found from the home page or the footer; none was read. | planned |
| Uzbekistan | `uz-constitution-site-constitution` | https://constitution.uz/uploads/constitution_oz.pdf | uz | Source must be named; the operator of the site is not identified, so the publisher cannot be assessed. Footer of constitution.uz: "© 2015 - 2026 – www.constitution.uz Sayt ma'lumotlaridan foydalanganda manba ko'rsatilishi shart" ("when using the site's data the source must be named"). The "Loyiha haqida" page names the project's aim and links the President, Senate and Legislative Chamber sites, but not an operator. | planned |

## Countries not in the sweep

Argentina, Australia, Canada, Croatia, the Czech Republic, Denmark, Estonia, Finland, France, Germany, Ireland, Italy, Latvia, Liechtenstein,
Luxembourg, the Netherlands, Poland, Slovenia, Spain, Sweden, Switzerland, the United Kingdom, the United States and Uruguay already have an
active legislation source that includes their constitution (see `docs/corpus.md`).
