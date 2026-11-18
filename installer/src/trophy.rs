use crate::{
    bail,
    update::{parse_json, Json},
    util::{self, Result},
};
use std::{collections::BTreeMap, fs, path::Path};

pub const STORAGE: &str = "gamedata/trophies";
const MAX_TROPHIES: u32 = 128;

pub struct Trophy {
    pub id: u32,
    pub grade: char,
    pub platinum: i32,
    pub hidden: bool,
    pub name: String,
    pub detail: String,
}

pub struct Catalog {
    pub communication_id: String,
    pub number: u8,
    pub trophies: Vec<Trophy>,
}

pub fn read_catalog(disc: &Path) -> Result<Catalog> {
    let mut packs = Vec::new();
    for folder in fs::read_dir(disc.join("PS3_GAME/TROPDIR"))? {
        let trp = folder?.path().join("TROPHY.TRP");
        if trp.is_file() {
            packs.push(trp);
        }
    }
    if packs.len() != 1 {
        bail!(
            "expected one trophy pack in PS3_GAME/TROPDIR, found {}",
            packs.len()
        );
    }
    let bytes = fs::read(&packs[0])?;
    let Some(xml) = pack_entry(&bytes, "TROP_01.SFM").or_else(|| pack_entry(&bytes, "TROP.SFM"))
    else {
        bail!("no readable English trophy list in TROPHY.TRP");
    };
    parse_catalog(xml)
}

fn pack_entry<'a>(data: &'a [u8], name: &str) -> Option<&'a str> {
    let u32_at = |at| Some(u32::from_be_bytes(data.get(at..at + 4)?.try_into().ok()?));
    if u32_at(0)? != 0xdca24d00 || u32_at(20)? != 64 {
        return None;
    }
    let count = u32_at(16)? as usize;
    let table_size = count.checked_mul(64)?.checked_add(64)?;
    let table = data.get(64..table_size)?;
    for row in table.chunks_exact(64) {
        let end = row[..32].iter().position(|&b| b == 0).unwrap_or(32);
        if &row[..end] == name.as_bytes() {
            let pos = usize::try_from(u64::from_be_bytes(row[32..40].try_into().ok()?)).ok()?;
            let len = usize::try_from(u64::from_be_bytes(row[40..48].try_into().ok()?)).ok()?;
            return std::str::from_utf8(data.get(pos..pos.checked_add(len)?)?).ok();
        }
    }
    None
}

fn element<'a>(xml: &'a str, tag: &str) -> Option<&'a str> {
    let (_, tail) = xml.split_once(&format!("<{tag}>"))?;
    let (body, _) = tail.split_once(&format!("</{tag}>"))?;
    Some(body.trim())
}

fn text(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    let mut pieces = value.split('&');
    out.push_str(pieces.next().unwrap_or(""));
    for part in pieces {
        let decoded = part.split_once(';').and_then(|(entity, rest)| {
            let ch = match entity {
                "amp" => '&',
                "lt" => '<',
                "gt" => '>',
                "quot" => '"',
                "apos" => '\'',
                x if x.starts_with("#x") => char::from_u32(u32::from_str_radix(&x[2..], 16).ok()?)?,
                x if x.starts_with('#') => char::from_u32(x[1..].parse().ok()?)?,
                _ => return None,
            };
            Some((ch, rest))
        });
        if let Some((ch, rest)) = decoded {
            out.push(ch);
            out.push_str(rest);
        } else {
            out.push('&');
            out.push_str(part);
        }
    }
    out.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn parse_catalog(xml: &str) -> Result<Catalog> {
    let invalid = || util::Error("invalid trophy metadata in TROPHY.TRP".into());
    let (communication_id, number) = element(xml, "npcommid")
        .and_then(|s| s.split_once('_'))
        .ok_or_else(invalid)?;
    if communication_id.len() != 9 || !communication_id.bytes().all(|c| c.is_ascii_alphanumeric()) {
        return Err(invalid());
    }
    let number = number.parse::<u8>().map_err(|_| invalid())?;
    let mut trophies = BTreeMap::new();
    for el in xml.split("<trophy ").skip(1) {
        let (attributes, body) = el.split_once('>').ok_or_else(invalid)?;
        let attribute = |name: &str| {
            attributes.split_ascii_whitespace().find_map(|pair| {
                let (k, v) = pair.split_once('=')?;
                (k == name).then(|| v.trim_matches('"'))
            })
        };
        let id: u32 = attribute("id")
            .and_then(|v| v.parse().ok())
            .ok_or_else(invalid)?;
        let grade = match attribute("ttype") {
            Some("P") => 'P',
            Some("G") => 'G',
            Some("S") => 'S',
            Some("B") => 'B',
            _ => return Err(invalid()),
        };
        let platinum = attribute("pid")
            .and_then(|v| v.parse().ok())
            .ok_or_else(invalid)?;
        let (body, _) = body.split_once("</trophy>").ok_or_else(invalid)?;
        let trophy = Trophy {
            id,
            grade,
            platinum,
            hidden: attribute("hidden") == Some("yes"),
            name: text(element(body, "name").ok_or_else(invalid)?),
            detail: text(element(body, "detail").unwrap_or("")),
        };
        if id >= MAX_TROPHIES || trophies.insert(id, trophy).is_some() {
            return Err(invalid());
        }
    }
    if trophies.is_empty() {
        return Err(invalid());
    }
    for trophy in trophies.values() {
        if trophy.platinum >= 0
            && !trophies
                .get(&(trophy.platinum as u32))
                .is_some_and(|t| t.grade == 'P')
        {
            return Err(invalid());
        }
    }
    Ok(Catalog {
        communication_id: communication_id.into(),
        number,
        trophies: trophies.into_values().collect(),
    })
}

impl Catalog {
    pub fn write_runtime_catalog(&self, cwd: &Path) -> Result<()> {
        use std::fmt::Write;
        let mut rows = format!("TROPHIES1\t{}\t{}\n", self.communication_id, self.number);
        for t in &self.trophies {
            writeln!(
                rows,
                "{}\t{}\t{}\t{}\t{}\t{}",
                t.id,
                t.grade,
                t.platinum,
                u8::from(t.hidden),
                t.name,
                t.detail
            )
            .expect("writing to a string cannot fail");
        }
        util::write_file(&cwd.join(STORAGE).join("catalog.tsv"), rows.as_bytes())
    }

    pub fn unlocked(&self, cwd: &Path) -> Result<BTreeMap<u32, u64>> {
        let path = cwd
            .join(STORAGE)
            .join(format!("{}.json", self.communication_id));
        let bytes = match fs::read(path) {
            Ok(bytes) => bytes,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(BTreeMap::new()),
            Err(e) => return Err(e.into()),
        };
        let parsed = parse_json(&bytes)?;
        let Some(Json::Arr(entries)) = parsed.get("trophies") else {
            bail!("invalid local trophy history");
        };
        let mut unlocked = BTreeMap::new();
        for entry in entries {
            let (Some(Json::Num(id)), Some(Json::Num(timestamp))) =
                (entry.get("id"), entry.get("timestamp"))
            else {
                bail!("invalid trophy unlock entry");
            };
            if *id >= 0.0 && *id < f64::from(MAX_TROPHIES) && id.fract() == 0.0 {
                unlocked.insert(*id as u32, *timestamp as u64);
            }
        }
        Ok(unlocked)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn catalog_keeps_hidden_and_platinum_relationships() {
        let data = r#"<npcommid>NPWR00001_00</npcommid>
<trophy id="000" hidden="no" ttype="P" pid="-1"><name>Platinum</name></trophy>
<trophy id="002" hidden="yes" ttype="B" pid="000"><name>A &amp; B</name><detail>Line 1
Line 2</detail></trophy>"#;
        let catalog = parse_catalog(data).unwrap();
        assert_eq!(catalog.communication_id, "NPWR00001");
        let t = &catalog.trophies[1];
        assert!(t.hidden);
        assert_eq!((t.id, t.grade, t.platinum), (2, 'B', 0));
        assert_eq!(t.name, "A & B");
        assert_eq!(t.detail, "Line 1 Line 2");
        assert!(parse_catalog(&data.replace("pid=\"000\"", "pid=\"001\"")).is_err());
    }
}
