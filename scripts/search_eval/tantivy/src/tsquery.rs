//! tsquery trees: parse PostgreSQL's tsquery text output (mode "pg"), build tsqueries the way
//! websearch_to_tsquery / phraseto_tsquery do (mode "native"), print them in PostgreSQL's format.
//!
//! The websearch grammar copied from PostgreSQL 16 (src/backend/utils/adt/tsquery.c,
//! gettoken_query_websearch and parse_or_operator):
//! * an operand is a run of characters up to whitespace, `"` or one of `! & | ( ) <`; the whole
//!   run is analysed and its lexemes are joined with `<->` (with `<N>` over removed stopwords),
//!   so `401(k)` is `'401' & 'k'` while `121-1` is `'121' <-> '-1'`;
//! * `"..."` is one operand (up to the next `"` or the end): a phrase;
//! * `-` where an operand is expected negates it; `or` (any case) between operands, followed by
//!   something other than a letter, digit, `-` or `_`, is OR; anything else is an implicit AND;
//! * `! & | ( ) <` are skipped (never syntax errors); precedence NOT > AND > OR;
//! * stopword placeholders are removed afterwards exactly like clean_stopwords_intree.

use crate::analyzer::Config;

pub const W_A: u8 = 8;
pub const W_B: u8 = 4;
pub const W_C: u8 = 2;
pub const W_D: u8 = 1;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Node {
    Val { lex: String, prefix: bool, weights: u8 },
    /// Placeholder for a removed stopword (QI_VALSTOP); removed by `clean_stopwords`.
    Stop,
    Not(Box<Node>),
    And(Box<Node>, Box<Node>),
    Or(Box<Node>, Box<Node>),
    Phrase(Box<Node>, Box<Node>, u16),
}

pub fn val(lex: &str) -> Node {
    Node::Val { lex: lex.to_string(), prefix: false, weights: 0 }
}

// ------------------------------------------------------------------ printing

fn priority(n: &Node) -> u8 {
    match n {
        Node::Not(_) => 4,
        Node::Phrase(..) => 3,
        Node::And(..) => 2,
        Node::Or(..) => 1,
        _ => 5,
    }
}

fn quote(lex: &str, out: &mut String) {
    out.push('\'');
    for c in lex.chars() {
        match c {
            '\'' => out.push_str("''"),
            '\\' => out.push_str("\\\\"),
            _ => out.push(c),
        }
    }
    out.push('\'');
}

fn infix(n: &Node, parent: u8, right_phrase: bool, out: &mut String) {
    match n {
        Node::Val { lex, prefix, weights } => {
            quote(lex, out);
            if *prefix || *weights != 0 {
                out.push(':');
                if *prefix {
                    out.push('*');
                }
                for (bit, ch) in [(W_A, 'A'), (W_B, 'B'), (W_C, 'C'), (W_D, 'D')] {
                    if weights & bit != 0 {
                        out.push(ch);
                    }
                }
            }
        }
        Node::Stop => out.push_str("<stop>"),
        Node::Not(child) => {
            let p = 4;
            if p < parent {
                out.push_str("( ");
            }
            out.push('!');
            infix(child, p, false, out);
            if p < parent {
                out.push_str(" )");
            }
        }
        Node::And(l, r) | Node::Or(l, r) | Node::Phrase(l, r, _) => {
            let p = priority(n);
            let is_phrase = matches!(n, Node::Phrase(..));
            let paren = p < parent || (is_phrase && right_phrase);
            if paren {
                out.push_str("( ");
            }
            infix(l, p, false, out);
            match n {
                Node::Or(..) => out.push_str(" | "),
                Node::And(..) => out.push_str(" & "),
                Node::Phrase(_, _, 1) => out.push_str(" <-> "),
                Node::Phrase(_, _, d) => out.push_str(&format!(" <{d}> ")),
                _ => unreachable!(),
            }
            infix(r, p, is_phrase, out);
            if paren {
                out.push_str(" )");
            }
        }
    }
}

/// PostgreSQL's text form of a tsquery ("" for the empty query).
pub fn to_text(q: &Option<Node>) -> String {
    let mut out = String::new();
    if let Some(n) = q {
        infix(n, 0, false, &mut out);
    }
    out
}

// ------------------------------------------------------------------ parsing PostgreSQL output

struct TextParser<'a> {
    s: &'a [u8],
    i: usize,
}

impl TextParser<'_> {
    fn ws(&mut self) {
        while self.i < self.s.len() && (self.s[self.i] as char).is_ascii_whitespace() {
            self.i += 1;
        }
    }

    fn peek(&mut self) -> Option<u8> {
        self.ws();
        self.s.get(self.i).copied()
    }

    fn or(&mut self) -> Result<Node, String> {
        let mut left = self.and()?;
        while self.peek() == Some(b'|') {
            self.i += 1;
            let right = self.and()?;
            left = Node::Or(Box::new(left), Box::new(right));
        }
        Ok(left)
    }

    fn and(&mut self) -> Result<Node, String> {
        let mut left = self.phrase()?;
        while self.peek() == Some(b'&') {
            self.i += 1;
            let right = self.phrase()?;
            left = Node::And(Box::new(left), Box::new(right));
        }
        Ok(left)
    }

    fn phrase(&mut self) -> Result<Node, String> {
        let mut left = self.unary()?;
        while self.peek() == Some(b'<') {
            self.i += 1;
            let start = self.i;
            while self.i < self.s.len() && self.s[self.i] != b'>' {
                self.i += 1;
            }
            let inner = std::str::from_utf8(&self.s[start..self.i]).map_err(|e| e.to_string())?;
            self.i += 1;
            let dist: u16 = if inner == "-" { 1 } else { inner.trim().parse().map_err(|_| format!("bad distance <{inner}>"))? };
            let right = self.unary()?;
            left = Node::Phrase(Box::new(left), Box::new(right), dist);
        }
        Ok(left)
    }

    fn unary(&mut self) -> Result<Node, String> {
        match self.peek() {
            Some(b'!') => {
                self.i += 1;
                Ok(Node::Not(Box::new(self.unary()?)))
            }
            Some(b'(') => {
                self.i += 1;
                let n = self.or()?;
                if self.peek() != Some(b')') {
                    return Err("missing )".into());
                }
                self.i += 1;
                Ok(n)
            }
            Some(b'\'') => {
                self.i += 1;
                let mut bytes = Vec::new();
                loop {
                    match self.s.get(self.i) {
                        None => return Err("unterminated lexeme".into()),
                        Some(b'\'') if self.s.get(self.i + 1) == Some(&b'\'') => {
                            bytes.push(b'\'');
                            self.i += 2;
                        }
                        Some(b'\'') => {
                            self.i += 1;
                            break;
                        }
                        Some(b'\\') if self.i + 1 < self.s.len() => {
                            bytes.push(self.s[self.i + 1]);
                            self.i += 2;
                        }
                        Some(&b) => {
                            bytes.push(b);
                            self.i += 1;
                        }
                    }
                }
                let lex = String::from_utf8(bytes).map_err(|e| e.to_string())?;
                let (mut prefix, mut weights) = (false, 0u8);
                if self.s.get(self.i) == Some(&b':') {
                    self.i += 1;
                    while let Some(&b) = self.s.get(self.i) {
                        match b.to_ascii_uppercase() {
                            b'*' => prefix = true,
                            b'A' => weights |= W_A,
                            b'B' => weights |= W_B,
                            b'C' => weights |= W_C,
                            b'D' => weights |= W_D,
                            _ => break,
                        }
                        self.i += 1;
                    }
                }
                Ok(Node::Val { lex, prefix, weights })
            }
            other => Err(format!("unexpected {:?} at byte {}", other.map(|b| b as char), self.i)),
        }
    }
}

/// Parses tsquery text as PostgreSQL prints it (`'a' & ( 'b' | !'c' ) <2> 'd':*`).
pub fn parse_text(text: &str) -> Result<Option<Node>, String> {
    let mut p = TextParser { s: text.as_bytes(), i: 0 };
    if p.peek().is_none() {
        return Ok(None);
    }
    let n = p.or()?;
    if p.peek().is_some() {
        return Err(format!("trailing input at byte {}", p.i));
    }
    Ok(Some(n))
}

// ------------------------------------------------------------------ stopword cleanup

/// clean_stopwords_intree (src/backend/utils/adt/tsquery_cleanup.c).
fn clean(node: Node, ladd: &mut u32, radd: &mut u32) -> Option<Node> {
    *ladd = 0;
    *radd = 0;
    match node {
        Node::Val { .. } => Some(node),
        Node::Stop => None,
        Node::Not(child) => clean(*child, ladd, radd).map(|c| Node::Not(Box::new(c))),
        other => {
            let (l, r, is_phrase, dist, is_and) = match other {
                Node::And(l, r) => (l, r, false, 0u32, true),
                Node::Or(l, r) => (l, r, false, 0, false),
                Node::Phrase(l, r, d) => (l, r, true, d as u32, false),
                _ => unreachable!(),
            };
            let (mut lladd, mut lradd, mut rladd, mut rradd) = (0, 0, 0, 0);
            let left = clean(*l, &mut lladd, &mut lradd);
            let right = clean(*r, &mut rladd, &mut rradd);
            match (left, right) {
                (None, None) => {
                    if is_phrase {
                        let v = lladd + dist + rradd;
                        *ladd = v;
                        *radd = v;
                    }
                    None
                }
                (None, Some(right)) => {
                    if is_phrase {
                        *ladd = lladd + dist + rladd;
                    } else {
                        *ladd = rladd;
                    }
                    *radd = rradd;
                    Some(right)
                }
                (Some(left), None) => {
                    *ladd = lladd;
                    *radd = if is_phrase { lradd + dist + rradd } else { lradd };
                    Some(left)
                }
                (Some(left), Some(right)) => {
                    if is_phrase {
                        let d = (dist + lradd + rladd).min(u16::MAX as u32) as u16;
                        *ladd = lladd;
                        *radd = rradd;
                        Some(Node::Phrase(Box::new(left), Box::new(right), d))
                    } else if is_and {
                        Some(Node::And(Box::new(left), Box::new(right)))
                    } else {
                        Some(Node::Or(Box::new(left), Box::new(right)))
                    }
                }
            }
        }
    }
}

pub fn clean_stopwords(q: Option<Node>) -> Option<Node> {
    let (mut l, mut r) = (0, 0);
    q.and_then(|n| clean(n, &mut l, &mut r))
}

// ------------------------------------------------------------------ building queries natively

/// pushval_morph with OP_PHRASE: the lexemes of one operand joined with <-> (left-deep), with
/// stopword placeholders for interior gaps; Stop when no lexeme is left.
pub fn operand(cfg: &Config, text: &str) -> Node {
    let words = cfg.words(text);
    let mut node: Option<Node> = None;
    let mut prev: u32 = 0;
    for (lex, pos) in words {
        if prev > 0 && prev + 1 < pos {
            while prev + 1 < pos {
                node = Some(match node {
                    Some(n) => Node::Phrase(Box::new(n), Box::new(Node::Stop), 1),
                    None => Node::Stop,
                });
                prev += 1;
            }
        }
        prev = pos;
        node = Some(match node {
            Some(n) => Node::Phrase(Box::new(n), Box::new(val(&lex)), 1),
            None => val(&lex),
        });
    }
    node.unwrap_or(Node::Stop)
}

/// phraseto_tsquery(cfg, text).
pub fn phraseto(cfg: &Config, text: &str) -> Option<Node> {
    clean_stopwords(Some(operand(cfg, text)))
}

/// The `||` operator on tsqueries (an empty side yields the other side).
pub fn or(a: Option<Node>, b: Option<Node>) -> Option<Node> {
    match (a, b) {
        (None, b) => b,
        (a, None) => a,
        (Some(a), Some(b)) => Some(Node::Or(Box::new(a), Box::new(b))),
    }
}

#[derive(Debug)]
enum WTok {
    Val(Node),
    Not,
    And,
    Or,
}

fn is_operator_char(c: char) -> bool {
    matches!(c, '!' | '&' | '|' | '(' | ')' | '<')
}

/// parse_or_operator: "or" followed by a non-word character and, later, something non-blank.
fn is_or_operator(s: &[char], i: usize) -> bool {
    if i + 2 > s.len() || !s[i].eq_ignore_ascii_case(&'o') || !s[i + 1].eq_ignore_ascii_case(&'r') {
        return false;
    }
    let next = i + 2;
    if next >= s.len() {
        return false;
    }
    let c = s[next];
    if c == '-' || c == '_' || c.is_alphanumeric() {
        return false;
    }
    let mut k = next + 1;
    loop {
        if k >= s.len() {
            return false;
        }
        if !s[k].is_whitespace() {
            return true;
        }
        k += 1;
    }
}

fn websearch_tokens(cfg: &Config, q: &str) -> Vec<WTok> {
    let s: Vec<char> = q.chars().collect();
    let n = s.len();
    let mut i = 0;
    let mut toks = Vec::new();
    let mut wait_operand = true;
    while i < n {
        let c = s[i];
        if wait_operand {
            if c == '-' {
                i += 1;
                toks.push(WTok::Not);
            } else if c == '"' {
                let start = i + 1;
                let mut j = start;
                while j < n && s[j] != '"' {
                    j += 1;
                }
                let text: String = s[start..j].iter().collect();
                toks.push(WTok::Val(operand(cfg, &text)));
                i = if j < n { j + 1 } else { j };
                wait_operand = false;
            } else if is_operator_char(c) || c.is_whitespace() {
                i += 1;
            } else {
                let start = i;
                let mut j = i;
                while j < n && !s[j].is_whitespace() && s[j] != '"' && !is_operator_char(s[j]) {
                    j += 1;
                }
                let text: String = s[start..j].iter().collect();
                toks.push(WTok::Val(operand(cfg, &text)));
                i = j;
                wait_operand = false;
            }
        } else if c == '"' {
            toks.push(WTok::And);
            wait_operand = true;
        } else if is_or_operator(&s, i) {
            i += 2;
            toks.push(WTok::Or);
            wait_operand = true;
        } else if c.is_whitespace() || is_operator_char(c) {
            i += 1;
        } else {
            toks.push(WTok::And);
            wait_operand = true;
        }
    }
    toks
}

struct WParser {
    toks: std::vec::IntoIter<WTok>,
    peeked: Option<WTok>,
}

impl WParser {
    fn peek(&mut self) -> Option<&WTok> {
        if self.peeked.is_none() {
            self.peeked = self.toks.next();
        }
        self.peeked.as_ref()
    }

    fn next(&mut self) -> Option<WTok> {
        self.peek();
        self.peeked.take()
    }

    fn or(&mut self) -> Node {
        let mut left = self.and();
        while matches!(self.peek(), Some(WTok::Or)) {
            self.next();
            let right = self.and();
            left = Node::Or(Box::new(left), Box::new(right));
        }
        left
    }

    fn and(&mut self) -> Node {
        let mut left = self.not();
        while matches!(self.peek(), Some(WTok::And)) {
            self.next();
            let right = self.not();
            left = Node::And(Box::new(left), Box::new(right));
        }
        left
    }

    fn not(&mut self) -> Node {
        match self.peek() {
            Some(WTok::Not) => {
                self.next();
                Node::Not(Box::new(self.not()))
            }
            Some(WTok::Val(_)) => match self.next() {
                Some(WTok::Val(n)) => n,
                _ => unreachable!(),
            },
            // a missing operand (dangling "-" or "or") is a placeholder that cleanup removes
            _ => Node::Stop,
        }
    }
}

/// websearch_to_tsquery(cfg, q).
pub fn websearch(cfg: &Config, q: &str) -> Option<Node> {
    let toks = websearch_tokens(cfg, q);
    if toks.is_empty() {
        return None;
    }
    let mut p = WParser { toks: toks.into_iter(), peeked: None };
    let tree = p.or();
    clean_stopwords(Some(tree))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::analyzer::config;

    fn ws(q: &str) -> String {
        to_text(&websearch(config("english"), q))
    }

    #[test]
    fn websearch_matches_postgres_16() {
        // expected values: SELECT websearch_to_tsquery('english', q) on PostgreSQL 16
        let cases = [
            ("401(k)", "'401' & 'k'"),
            ("6(1)(f)", "'6' & '1' & 'f'"),
            ("§ 1983", "'1983'"),
            ("L. 121-1", "'l' & '121' <-> '-1'"),
            ("\"right to vote\" or privacy -tax", "'right' <2> 'vote' | 'privaci' & !'tax'"),
            ("self-defense", "'self-defens' <-> 'self' <-> 'defens'"),
            ("\"a -b\" OR", "'b'"),
            ("U.S.C. 1983", "'u.s.c' & '1983'"),
            ("the", ""),
            ("-foo", "!'foo'"),
            ("42 U.S.C. § 1983(a)(2)", "'42' & 'u.s.c' & '1983' & '2'"),
            ("O'Brien v. Smith", "'o' <-> 'brien' & 'v' & 'smith'"),
            ("x - y", "'x' & !'y'"),
            ("--x", "!!'x'"),
            ("-\"right to vote\"", "!( 'right' <2> 'vote' )"),
            ("\"right to vote", "'right' <2> 'vote'"),
            ("x OR y z", "'x' | 'y' & 'z'"),
            ("or x", "'x'"),
            ("x or or y", "'x' | 'y'"),
            ("x | y", "'x' & 'y'"),
            ("x<->y", "'x' & !'y'"),
            ("x OR", "'x'"),
            ("x -or y", "'x' & 'y'"),
            ("x\"y z\"w", "'x' & 'y' <-> 'z' & 'w'"),
            ("x or-y", "'x' & 'or-i' <2> 'y'"),
            ("x or.y", "'x' | 'y'"),
            ("x or_y", "'x' & 'y'"),
            ("x or -", "'x'"),
            ("- or x", "'x'"),
            ("x -y or z", "'x' & !'y' | 'z'"),
            ("x (or) y", "'x' | 'y'"),
            ("cats-dogs \"pre-1990 rules\"", "'cats-dog' <-> 'cat' <-> 'dog' & 'pre' <-> '-1990' <-> 'rule'"),
        ];
        for (q, expected) in cases {
            assert_eq!(ws(q), expected, "query {q:?}");
        }
    }

    #[test]
    fn text_roundtrip() {
        for t in [
            "'a' & 'b'",
            "'a' | 'b' & !'c'",
            "!( 'right' <2> 'vote' )",
            "'a' <-> ( 'b' <-> 'c' )",
            "( 'a' | 'b' ) <-> 'c'",
            "'o''brien':*AB & 'x\\\\y'",
            "!!'x'",
        ] {
            assert_eq!(to_text(&parse_text(t).unwrap()), t);
        }
        assert_eq!(parse_text("").unwrap(), None);
    }

    #[test]
    fn phraseto_drops_edge_stopwords() {
        assert_eq!(to_text(&phraseto(config("english"), "the right to the vote of")), "'right' <3> 'vote'");
        assert_eq!(to_text(&phraseto(config("english"), "the of")), "");
    }
}
