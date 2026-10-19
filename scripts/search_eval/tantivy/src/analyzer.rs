//! "native" analysis: a copy of PostgreSQL's text-search pipeline without PostgreSQL.
//!
//! * `tokenize` approximates PostgreSQL's default parser (src/backend/tsearch/wparser_def.c) for
//!   the token types that occur in legal text: words, numbers (uint, signed int, float, version),
//!   alphanumeric words (numword), hyphenated words (compound + parts, each with its own
//!   position), host/file names ("u.s.c", "and/or", "www.x.gov") and simple URLs. HTML tags,
//!   entities, e-mail local parts with dots and scientific floats are not emulated.
//! * `Config::lexize` copies the dictionaries: Snowball stemmer + PostgreSQL's own stopword file
//!   for letter tokens, the `simple` dictionary (lowercase only) for everything else, and for the
//!   `simple` configuration.
//! * `to_tsvector`/`passage_tsvector` copy PostgreSQL's tsvector rules: positions clamped to
//!   16383, at most 256 positions per lexeme, setweight(context,'B') || setweight(text,'A') with
//!   the text positions shifted by the context's highest position.

use rust_stemmers::{Algorithm, Stemmer};
use std::collections::{BTreeMap, HashSet};
use std::sync::OnceLock;

/// PostgreSQL MAXENTRYPOS (1 << 14): positions are clamped to MAXENTRYPOS - 1.
pub const MAX_POS: u32 = (1 << 14) - 1;
/// PostgreSQL MAXNUMPOS: positions kept per lexeme.
pub const MAX_NUM_POS: usize = 256;
/// PostgreSQL MAXSTRLEN: longer tokens are skipped by parsetext (with a NOTICE).
const MAX_STR_LEN: usize = (1 << 11) - 1;

pub fn limit_pos(p: u32) -> u32 {
    p.min(MAX_POS)
}

/// One token of the default parser, before dictionaries.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RawToken {
    pub text: String,
    /// Letter-only token (asciiword, word, hword parts and compounds of letters): goes to the
    /// language's stemmer. Everything else goes to the `simple` dictionary.
    pub stemmable: bool,
}

fn is_alpha(c: char) -> bool {
    c.is_alphabetic()
}

fn is_digit(c: char) -> bool {
    c.is_ascii_digit()
}

fn is_alnum(c: char) -> bool {
    is_alpha(c) || is_digit(c)
}

fn run_end(s: &[char], mut i: usize, pred: fn(char) -> bool) -> usize {
    while i < s.len() && pred(s[i]) {
        i += 1;
    }
    i
}

fn collect(s: &[char], from: usize, to: usize) -> String {
    s[from..to].iter().collect()
}

/// Splits text into the tokens PostgreSQL's default parser would produce (approximation).
pub fn tokenize(text: &str) -> Vec<RawToken> {
    let s: Vec<char> = text.chars().collect();
    let n = s.len();
    let mut out = Vec::new();
    let mut i = 0;
    let push = |out: &mut Vec<RawToken>, t: String, stemmable: bool| {
        out.push(RawToken { text: t, stemmable });
    };
    while i < n {
        let c = s[i];
        // "word.. next": PostgreSQL reads the two dots after an alphanumeric token as a file name
        if c == '.' && i > 0 && is_alnum(s[i - 1]) && i + 1 < n && s[i + 1] == '.' {
            if i + 2 == n || (s[i + 2] != '.' && !is_alnum(s[i + 2])) {
                push(&mut out, "..".to_string(), false);
                i += 2;
                continue;
            }
        }
        // "/path" at a token start is a file name
        if c == '/' && (i == 0 || !is_alnum(s[i - 1])) && i + 1 < n && is_alnum(s[i + 1]) {
            let k = file_chain_end(&s, i);
            push(&mut out, collect(&s, i, k), false);
            i = k;
            continue;
        }
        let signed = (c == '-' || c == '+') && i + 1 < n && is_digit(s[i + 1]);
        let run = if is_alnum(c) { run_end(&s, i, is_alnum) } else { i };
        let numeric = signed || (is_digit(c) && s[i..run].iter().all(|c| is_digit(*c)));
        if numeric {
            // uint / signed int / float / version: digits ('.' digits)*
            let start = i;
            let mut j = run_end(&s, i + usize::from(signed), is_digit);
            while j + 1 < n && s[j] == '.' && is_digit(s[j + 1]) {
                j = run_end(&s, j + 1, is_digit);
            }
            if !signed && j + 1 < n && s[j] == '/' && is_alnum(s[j + 1]) {
                // file name such as "1/2"
                let k = file_chain_end(&s, j);
                push(&mut out, collect(&s, start, k), false);
                i = k;
                continue;
            }
            push(&mut out, collect(&s, start, j), false);
            i = j;
            continue;
        }
        if is_alnum(c) {
            let start = i;
            let j = run;
            // protocol "http://": not indexed, the rest is parsed as usual
            if j + 3 < n && s[j] == ':' && s[j + 1] == '/' && s[j + 2] == '/' {
                let word: String = collect(&s, start, j).to_lowercase();
                if word == "http" || word == "https" || word == "ftp" {
                    i = j + 3;
                    continue;
                }
            }
            // e-mail: local part (alphanumeric runs joined by '.') '@' host
            {
                let mut k = j;
                while k + 1 < n && s[k] == '.' && is_alnum(s[k + 1]) {
                    k = run_end(&s, k + 1, is_alnum);
                }
                if k + 1 < n && s[k] == '@' && is_alnum(s[k + 1]) {
                    let h = host_end(&s, k + 1);
                    if is_host(&s[k + 1..h]) {
                        push(&mut out, collect(&s, start, h), false);
                        i = h;
                        continue;
                    }
                }
            }
            // host / file name: "u.s.c", "www.x.gov", "and/or", "sec.5", "1b.3"; host + path = URL
            if j + 1 < n && (s[j] == '.' || s[j] == '/') && is_alnum(s[j + 1]) {
                let h = host_end(&s, start);
                if h + 1 < n && s[h] == '/' && is_host(&s[start..h]) && s[h + 1].is_ascii_graphic() && !matches!(s[h + 1], '"' | '<' | '>') {
                    let mut e = h + 1;
                    while e < n && s[e].is_ascii_graphic() && !matches!(s[e], '"' | '<' | '>') {
                        e += 1;
                    }
                    push(&mut out, collect(&s, start, e), false);
                    push(&mut out, collect(&s, start, h), false);
                    push(&mut out, collect(&s, h, e), false);
                    i = e;
                    continue;
                }
                let k = file_chain_end(&s, j);
                push(&mut out, collect(&s, start, k), false);
                i = k;
                continue;
            }
            // hyphenated word: parts must not be pure digits ("a-1" is "a" then int "-1")
            let mut parts = vec![(start, j)];
            let mut k = j;
            while k + 1 < n && s[k] == '-' && is_alnum(s[k + 1]) {
                let m = run_end(&s, k + 1, is_alnum);
                if s[k + 1..m].iter().all(|c| is_digit(*c)) {
                    break;
                }
                parts.push((k + 1, m));
                k = m;
            }
            if parts.len() > 1 {
                let all_letters = parts.iter().all(|&(a, b)| s[a..b].iter().all(|c| is_alpha(*c)));
                push(&mut out, collect(&s, start, k), all_letters);
                for &(a, b) in &parts {
                    let letters = s[a..b].iter().all(|c| is_alpha(*c));
                    push(&mut out, collect(&s, a, b), letters);
                }
                i = k;
                continue;
            }
            // word (letters: stemmed) or numword (letters and digits: not stemmed)
            let letters = s[start..j].iter().all(|c| is_alpha(*c));
            push(&mut out, collect(&s, start, j), letters);
            i = j;
            continue;
        }
        i += 1;
    }
    out
}

/// End of a host name candidate: alphanumeric labels (with inner '-') joined by '.'.
fn host_end(s: &[char], start: usize) -> usize {
    let n = s.len();
    let mut h = run_end(s, start, is_alnum);
    while h + 1 < n && (s[h] == '.' || s[h] == '-') && is_alnum(s[h + 1]) {
        h = run_end(s, h + 1, is_alnum);
    }
    h
}

/// PostgreSQL host: at least two labels, the last one letters only and at least two long.
fn is_host(s: &[char]) -> bool {
    let text: String = s.iter().collect();
    let labels: Vec<&str> = text.split('.').collect();
    labels.len() >= 2
        && labels.iter().all(|l| !l.is_empty())
        && labels.last().is_some_and(|l| l.chars().count() >= 2 && l.chars().all(is_alpha))
}

/// End of a host/file chain starting at a '.' or '/' separator at `j`.
fn file_chain_end(s: &[char], mut j: usize) -> usize {
    let n = s.len();
    while j + 1 < n && matches!(s[j], '.' | '/' | '-' | '_') && is_alnum(s[j + 1]) {
        j = run_end(s, j + 1, is_alnum);
    }
    j
}

/// A text-search configuration (dictionary chain) copied from PostgreSQL.
pub struct Config {
    pub name: &'static str,
    stemmer: Option<Stemmer>,
    stopwords: HashSet<String>,
}

fn stoplist(raw: &str) -> HashSet<String> {
    raw.lines().map(|l| l.trim().to_lowercase()).filter(|l| !l.is_empty()).collect()
}

macro_rules! stop {
    ($f:literal) => {
        include_str!(concat!("../stopwords/", $f, ".stop"))
    };
}

/// The configurations of ulis.languages.SEARCH_CONFIGS plus `simple`.
pub const CONFIG_NAMES: [&str; 17] = [
    "danish",
    "dutch",
    "english",
    "finnish",
    "french",
    "german",
    "greek",
    "hungarian",
    "italian",
    "norwegian",
    "portuguese",
    "romanian",
    "russian",
    "simple",
    "spanish",
    "swedish",
    "turkish",
];

fn build_config(name: &'static str) -> Config {
    let (alg, stop): (Option<Algorithm>, &str) = match name {
        "danish" => (Some(Algorithm::Danish), stop!("danish")),
        "dutch" => (Some(Algorithm::Dutch), stop!("dutch")),
        "english" => (Some(Algorithm::English), stop!("english")),
        "finnish" => (Some(Algorithm::Finnish), stop!("finnish")),
        "french" => (Some(Algorithm::French), stop!("french")),
        "german" => (Some(Algorithm::German), stop!("german")),
        "greek" => (Some(Algorithm::Greek), ""),
        "hungarian" => (Some(Algorithm::Hungarian), stop!("hungarian")),
        "italian" => (Some(Algorithm::Italian), stop!("italian")),
        "norwegian" => (Some(Algorithm::Norwegian), stop!("norwegian")),
        "portuguese" => (Some(Algorithm::Portuguese), stop!("portuguese")),
        "romanian" => (Some(Algorithm::Romanian), ""),
        "russian" => (Some(Algorithm::Russian), stop!("russian")),
        "spanish" => (Some(Algorithm::Spanish), stop!("spanish")),
        "swedish" => (Some(Algorithm::Swedish), stop!("swedish")),
        "turkish" => (Some(Algorithm::Turkish), stop!("turkish")),
        _ => (None, ""),
    };
    Config { name, stemmer: alg.map(Stemmer::create), stopwords: stoplist(stop) }
}

/// The configuration for a regconfig name; unknown names behave as `simple` (like ulis.languages).
pub fn config(name: &str) -> &'static Config {
    static CONFIGS: OnceLock<Vec<Config>> = OnceLock::new();
    let all = CONFIGS.get_or_init(|| CONFIG_NAMES.iter().map(|n| build_config(n)).collect());
    all.iter().find(|c| c.name == name).unwrap_or_else(|| all.iter().find(|c| c.name == "simple").unwrap())
}

impl Config {
    /// The lexeme for one token, or None for a stopword (PostgreSQL dsnowball_lexize / dsimple_lexize).
    pub fn lexize(&self, tok: &RawToken) -> Option<String> {
        let lower = tok.text.to_lowercase();
        match (&self.stemmer, tok.stemmable) {
            (Some(stemmer), true) => {
                if lower.len() > 1000 {
                    Some(lower)
                } else if lower.is_empty() || self.stopwords.contains(&lower) {
                    None
                } else {
                    Some(stemmer.stem(&lower).into_owned())
                }
            }
            _ => Some(lower),
        }
    }

    /// (lexeme, position) for every non-stopword token; positions count stopwords and are clamped
    /// like PostgreSQL's parsetext (positions start at 1).
    pub fn words(&self, text: &str) -> Vec<(String, u32)> {
        let mut out = Vec::new();
        let mut pos: u32 = 0;
        for tok in tokenize(text) {
            if tok.text.len() >= MAX_STR_LEN {
                continue;
            }
            pos += 1;
            if let Some(lex) = self.lexize(&tok) {
                if !lex.is_empty() {
                    out.push((lex, limit_pos(pos)));
                }
            }
        }
        out
    }

    /// to_tsvector(config, text): lexeme -> sorted unique positions (at most 256).
    pub fn to_tsvector(&self, text: &str) -> BTreeMap<String, Vec<u32>> {
        let mut map: BTreeMap<String, Vec<u32>> = BTreeMap::new();
        for (lex, pos) in self.words(text) {
            map.entry(lex).or_default().push(pos);
        }
        for v in map.values_mut() {
            v.sort_unstable();
            v.dedup();
            v.truncate(MAX_NUM_POS);
        }
        map
    }
}

/// A lexeme position with its weight: true = A (text), false = B (context).
pub type WeightedPositions = Vec<(u32, bool)>;

/// setweight(to_tsvector(cfg, context), 'B') || setweight(to_tsvector(cfg, text), 'A').
pub fn passage_tsvector(cfg: &Config, context: &str, text: &str) -> BTreeMap<String, WeightedPositions> {
    let left = cfg.to_tsvector(context);
    let right = cfg.to_tsvector(text);
    let maxpos = left.values().flat_map(|v| v.iter().copied()).max().unwrap_or(0);
    let mut out: BTreeMap<String, WeightedPositions> = BTreeMap::new();
    for (lex, positions) in left {
        out.insert(lex, positions.into_iter().map(|p| (p, false)).collect());
    }
    for (lex, positions) in right {
        let dest = out.entry(lex).or_default();
        // tsvector_concat / add_pos: shift, clamp, stop at MAXNUMPOS or once the clamp is reached
        for p in positions {
            if dest.len() >= MAX_NUM_POS || dest.last().is_some_and(|&(q, _)| q == MAX_POS) {
                break;
            }
            dest.push((limit_pos(p + maxpos), true));
        }
    }
    out.retain(|_, v| !v.is_empty());
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lexemes(cfg: &str, text: &str) -> Vec<(String, u32)> {
        config(cfg).words(text)
    }

    fn toks(text: &str) -> Vec<String> {
        tokenize(text).into_iter().map(|t| t.text).collect()
    }

    #[test]
    fn legal_citations_tokenize_like_postgres() {
        assert_eq!(toks("401(k)"), ["401", "k"]);
        assert_eq!(toks("6(1)(f)"), ["6", "1", "f"]);
        assert_eq!(toks("§ 1983"), ["1983"]);
        assert_eq!(toks("L. 121-1"), ["L", "121", "-1"]);
        assert_eq!(toks("12-31-2020"), ["12", "-31", "-2020"]);
        assert_eq!(toks("5-year"), ["5", "year"]);
        assert_eq!(toks("§§ 1.1-1.5"), ["1.1", "-1.5"]);
        assert_eq!(toks("e.g. U.S.C. end.Next"), ["e.g", "U.S.C", "end.Next"]);
        assert_eq!(toks("and/or 1/2"), ["and/or", "1/2"]);
        assert_eq!(toks("N-1A"), ["N-1A", "N", "1A"]);
        assert_eq!(toks("a-1 pre-1990"), ["a", "-1", "pre", "-1990"]);
        assert_eq!(toks("3rd 12.5a 6.2.1"), ["3rd", "12.5", "a", "6.2.1"]);
        assert_eq!(toks("http://www.x.gov/a/b"), ["www.x.gov/a/b", "www.x.gov", "/a/b"]);
        assert_eq!(toks("see www.x.gov/a/b), and"), ["see", "www.x.gov/a/b),", "www.x.gov", "/a/b),", "and"]);
        assert_eq!(toks("http://x.gov U.S.C/x"), ["x.gov", "U.S.C/x"]);
        assert_eq!(toks("requests.. Expedited x... y"), ["requests", "..", "Expedited", "x", "y"]);
        assert_eq!(toks("1b.3 1765a-g /be"), ["1b.3", "1765a-g", "1765a", "g", "/be"]);
        assert_eq!(toks("a.resource@nrc.gov. x@y"), ["a.resource@nrc.gov", "x", "y"]);
    }

    #[test]
    fn hyphenated_words_index_compound_and_parts() {
        assert_eq!(
            lexemes("english", "self-defense law"),
            [("self-defens".into(), 1), ("self".into(), 2), ("defens".into(), 3), ("law".into(), 4)]
        );
    }

    #[test]
    fn stopwords_consume_positions() {
        assert_eq!(lexemes("english", "right to vote"), [("right".into(), 1), ("vote".into(), 3)]);
        // simple: no stopwords, no stemming (Welsh, CJK bigrams)
        assert_eq!(lexemes("simple", "Y Ddeddf"), [("y".into(), 1), ("ddeddf".into(), 2)]);
        assert_eq!(lexemes("simple", "個人 人情 情報"), [("個人".into(), 1), ("人情".into(), 2), ("情報".into(), 3)]);
    }

    #[test]
    fn passage_tsvector_shifts_text_after_context() {
        let tv = passage_tsvector(config("english"), "Part 1: Definitions.", "Definitions apply.");
        assert_eq!(tv["definit"], vec![(3, false), (4, true)]);
        assert_eq!(tv["appli"], vec![(5, true)]);
    }

    #[test]
    fn positions_clamp_like_postgres() {
        let text = "word ".repeat(20000);
        let tv = config("english").to_tsvector(&text);
        assert_eq!(tv["word"].len(), 256);
        let long: String = (0..17000).map(|i| format!("w{i} ")).collect();
        let tv = config("simple").to_tsvector(&long);
        assert_eq!(tv["w16999"], vec![MAX_POS]);
    }
}
