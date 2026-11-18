//! Pinned v9 IRDs for the disc releases accepted by Setup.
use crate::{
    hash,
    setup::{check_game, DiscKey, GameInfo, GameProblem, GameSource},
    update,
};
use aes::{
    cipher::{BlockEncrypt, KeyInit},
    Aes128,
};
use flate2::read::GzDecoder;
use std::{io::Read, path::Path};

const BASE: &str = "https://raw.githubusercontent.com/FlexBy420/playstation_3_ird_database/7c118d1ee1a2356be9cae4321f5bb2a715a33bf6/ps3.aldostools.org";
struct Record {
    title: &'static str,
    revision: &'static str,
    sha256: &'static str,
}
const RECORDS: &[Record] = &[
    Record {
        title: "BCUS98119",
        revision: "4FE043A1A99B403AC25E74367416E5F1",
        sha256: "134744d82656d32ab24f5b1d1a1e80791381eef1fdacc42f6bbf6dd42960e2ab",
    },
    Record {
        title: "BCUS98119",
        revision: "52369B84B9B56653399D31EBBD1D76A0",
        sha256: "9067efca7725ca6bc3662b75b7cdd5b3cda414c20d183e9a420042ddaeb574c4",
    },
    Record {
        title: "BCES00220",
        revision: "06DEBA344F51177AAAB62DBAC374558E",
        sha256: "c4ae9449d6f3a9e19f25afc065670c9b9bfd69c8b6f033e342284e50ccbb1e58",
    },
];

fn read_record(data: &[u8], title: &str) -> Option<[u8; 16]> {
    let mut ird = Vec::new();
    GzDecoder::new(data)
        .take(256 * 1024 + 1)
        .read_to_end(&mut ird)
        .ok()?;
    if ird.len() > 256 * 1024
        || ird.len() < 54
        || !ird.starts_with(b"3IRD\x09")
        || ird.get(5..14)? != title.as_bytes()
    {
        return None;
    }
    // The v9 trailer contains Data1, Data2, UID and CRC32, in that order.
    let offset = ird.len() - 40;
    let mut block: [u8; 16] = ird[offset..offset + 16].try_into().ok()?;
    let iv = [
        0x69, 0x47, 0x47, 0x72, 0xaf, 0x6f, 0xda, 0xb3, 0x42, 0x74, 0x3a, 0xef, 0xaa, 0x18, 0x62,
        0x87,
    ];
    let key = [
        0x38, 0x0b, 0xcf, 0x0b, 0x53, 0x45, 0x5b, 0x3c, 0x78, 0x17, 0xab, 0x4f, 0xa3, 0xba, 0x90,
        0xed,
    ];
    for (v, mask) in block.iter_mut().zip(iv) {
        *v ^= mask;
    }
    Aes128::new((&key).into()).encrypt_block((&mut block).into());
    Some(block)
}

fn fetch(record: &Record) -> Option<[u8; 16]> {
    let url = format!("{BASE}/{}-{}.ird", record.title, record.revision);
    let out = update::curl_program()
        .args([
            "-q",
            "-fsS",
            "--proto",
            "=https",
            "--max-time",
            "15",
            "--max-filesize",
            "262144",
            &url,
        ])
        .output()
        .ok()?;
    if !out.status.success() || hash::sha256_hex(&out.stdout) != record.sha256 {
        return None;
    }
    read_record(&out.stdout, record.title)
}

pub fn lookup_disc_key(path: &Path, titles: &[String]) -> Result<(GameInfo, GameSource), String> {
    find_match(path, titles, fetch)
}

fn find_match(
    path: &Path,
    titles: &[String],
    mut get: impl FnMut(&Record) -> Option<[u8; 16]>,
) -> Result<(GameInfo, GameSource), String> {
    let mut missing = false;
    for r in RECORDS
        .iter()
        .filter(|r| titles.is_empty() || titles.iter().any(|t| t == r.title))
    {
        let Some(key) = get(r) else {
            missing = true;
            continue;
        };
        let src = GameSource::Iso {
            path: path.to_owned(),
            key: Some(DiscKey::Online(key)),
        };
        match check_game(&src, &[r.title.to_string()]) {
            Ok(info) if info.encrypted => return Ok((info, src)),
            Ok(_) | Err(GameProblem::BadKey | GameProblem::WrongGame { .. }) => continue,
            Err(e) => return Err(e.to_string()),
        }
    }
    Err(if missing {
        "Could not download or verify all disc records. Retry or choose a disc key."
    } else {
        "No matching online key for this disc revision. Choose its disc key."
    }
    .into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    #[test]
    fn v9_data1_vector() {
        let mut bytes = b"3IRD\x09BCUS98119".to_vec();
        bytes.extend([0; 40]);
        let mut gz = flate2::write::GzEncoder::new(Vec::new(), Default::default());
        gz.write_all(&bytes).unwrap();
        let data = gz.finish().unwrap();
        assert_eq!(
            hash::hex(&read_record(&data, "BCUS98119").unwrap()),
            "e855c9af3b8be026fd97a7cad9c3f890"
        );
        assert!(read_record(&data, "BCES00220").is_none());
        assert!(read_record(b"invalid", "BCUS98119").is_none());
    }
    #[test]
    fn unavailable_records() {
        assert!(find_match(Path::new("renamed.iso"), &[], |_| None)
            .unwrap_err()
            .contains("download"));
    }
    #[test]
    #[ignore = "requires a supported encrypted ISO and network access"]
    fn live_disc() {
        let path = std::env::var_os("INFAMOUS_SETUP_GAME").unwrap();
        let (info, source) = lookup_disc_key(Path::new(&path), &[]).unwrap();
        assert!(info.encrypted);
        let GameSource::Iso {
            key: Some(DiscKey::Online(key)),
            ..
        } = &source
        else {
            panic!("lookup did not pass its key to the installer");
        };
        let path = Path::new(&path);
        assert!(find_match(path, &[], |_| Some([0; 16]))
            .unwrap_err()
            .contains("No matching"));
        // Exercise a failed candidate followed by the known matching key.
        let mut first = true;
        let recovered = find_match(path, &[], |r| {
            if first && r.title == "BCUS98119" {
                first = false;
                Some([0; 16])
            } else {
                Some(*key)
            }
        })
        .unwrap();
        assert_eq!(recovered.0.title_id, info.title_id);
        assert_eq!(check_game(&source, &[]).unwrap().title_id, info.title_id);
    }
}
