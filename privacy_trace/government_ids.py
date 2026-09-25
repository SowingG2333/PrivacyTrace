"""Declarative government-ID formats and deterministic profile evidence.

The generator treats identifiers as synthetic values, so this module does not
try to prove issuance or contact external registries. It does make every
configured document format explicit and decodes all profile-relevant fields
that are structurally present in the configured format. A single comparison
layer then checks dates, age, and sex for every country; country decoders never
perform those cross-profile comparisons themselves.
"""

from __future__ import annotations

import re
import string
import unicodedata
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence


Decoder = Callable[[str], "GovernmentIdEvidence"]


@dataclass(frozen=True)
class GovernmentIdEvidence:
    """Human-interpretable fields encoded by one normalized identifier."""

    birth_years: tuple[int, ...] = ()
    birth_month: int | None = None
    birth_day: int | None = None
    sex: str | None = None
    region_code: str | None = None
    citizenship_marker: str | None = None
    name_components: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    intrinsic_issues: tuple[str, ...] = ()

    def as_prompt_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.birth_years:
            payload["possible_encoded_birth_years"] = list(self.birth_years)
        if self.birth_month is not None:
            payload["encoded_birth_month"] = self.birth_month
        if self.birth_day is not None:
            payload["encoded_birth_day"] = self.birth_day
        if self.sex is not None:
            payload["encoded_sex"] = self.sex
        if self.region_code is not None:
            payload["encoded_region_code"] = self.region_code
        if self.citizenship_marker is not None:
            payload["encoded_citizenship_marker"] = self.citizenship_marker
        if self.name_components:
            payload["encoded_name_components"] = dict(self.name_components)
        return payload


@dataclass(frozen=True)
class GovernmentIdVariant:
    name: str
    pattern: re.Pattern[str]
    format_hint: str
    decoder: Decoder


@dataclass(frozen=True)
class GovernmentIdRule:
    country: str
    variants: tuple[GovernmentIdVariant, ...]

    @property
    def format_hint(self) -> str:
        return "; or ".join(variant.format_hint for variant in self.variants)


@dataclass(frozen=True)
class GovernmentIdInspection:
    country: str
    document_type: str | None
    normalized: str
    format_hint: str
    decoded_components: Mapping[str, Any]
    issues: tuple[str, ...]

    def as_prompt_dict(self) -> dict[str, Any]:
        return {
            "document_type": self.document_type,
            "normalized_government_id": self.normalized,
            "decoded_components": dict(self.decoded_components),
            "deterministic_issues": list(self.issues),
        }


def _opaque(_: str) -> GovernmentIdEvidence:
    return GovernmentIdEvidence()


def _evidence(
    *,
    birth_years: Sequence[int] = (),
    month: int | None = None,
    day: int | None = None,
    sex: str | None = None,
    region: str | None = None,
    citizenship: str | None = None,
    name_components: Mapping[str, str] | None = None,
    issues: Sequence[str] = (),
) -> GovernmentIdEvidence:
    return GovernmentIdEvidence(
        birth_years=tuple(int(value) for value in birth_years),
        birth_month=month,
        birth_day=day,
        sex=sex,
        region_code=region,
        citizenship_marker=citizenship,
        name_components=MappingProxyType(dict(name_components or {})),
        intrinsic_issues=tuple(issues),
    )


def _two_century_years(two_digit_year: int) -> tuple[int, int]:
    return (1900 + two_digit_year, 2000 + two_digit_year)


def _decode_china(value: str) -> GovernmentIdEvidence:
    return _evidence(
        birth_years=(int(value[6:10]),),
        month=int(value[10:12]),
        day=int(value[12:14]),
        sex="male" if int(value[16]) % 2 else "female",
        region=value[:6],
    )


def _decode_mexico(value: str) -> GovernmentIdEvidence:
    return _evidence(
        birth_years=_two_century_years(int(value[4:6])),
        month=int(value[6:8]),
        day=int(value[8:10]),
        sex="male" if value[10] == "H" else "female",
        region=value[11:13],
        name_components={
            "surname_and_given_name_code": value[:4],
            "internal_consonants": value[13:16],
        },
    )


def _decode_france(value: str) -> GovernmentIdEvidence:
    month = int(value[3:5])
    issues = () if 1 <= month <= 12 else ("encoded birth month is invalid",)
    return _evidence(
        birth_years=_two_century_years(int(value[1:3])),
        month=month,
        sex="male" if value[0] == "1" else "female",
        region=value[5:10],
        issues=issues,
    )


def _decode_poland(value: str) -> GovernmentIdEvidence:
    year = int(value[:2])
    encoded_month = int(value[2:4])
    if 1 <= encoded_month <= 12:
        century, month = 1900, encoded_month
    elif 21 <= encoded_month <= 32:
        century, month = 2000, encoded_month - 20
    elif 41 <= encoded_month <= 52:
        century, month = 2100, encoded_month - 40
    elif 61 <= encoded_month <= 72:
        century, month = 2200, encoded_month - 60
    elif 81 <= encoded_month <= 92:
        century, month = 1800, encoded_month - 80
    else:
        century, month = 1900, encoded_month
    return _evidence(
        birth_years=(century + year,),
        month=month,
        day=int(value[4:6]),
        sex="male" if int(value[9]) % 2 else "female",
    )


def _decode_south_korea(value: str) -> GovernmentIdEvidence:
    marker = int(value[6])
    century_by_marker = {
        9: 1800,
        0: 1800,
        1: 1900,
        2: 1900,
        5: 1900,
        6: 1900,
        3: 2000,
        4: 2000,
        7: 2000,
        8: 2000,
    }
    return _evidence(
        birth_years=(century_by_marker[marker] + int(value[:2]),),
        month=int(value[2:4]),
        day=int(value[4:6]),
        sex="male" if marker % 2 else "female",
        citizenship="citizen" if marker in {1, 2, 3, 4, 9, 0} else "foreigner",
    )


def _decode_indonesia(value: str) -> GovernmentIdEvidence:
    encoded_day = int(value[6:8])
    sex = "female" if encoded_day > 40 else "male"
    day = encoded_day - 40 if sex == "female" else encoded_day
    return _evidence(
        birth_years=_two_century_years(int(value[10:12])),
        month=int(value[8:10]),
        day=day,
        sex=sex,
        region=value[:6],
    )


def _decode_vietnam(value: str) -> GovernmentIdEvidence:
    marker = int(value[3])
    century = 1900 + (marker // 2) * 100
    return _evidence(
        birth_years=(century + int(value[4:6]),),
        sex="male" if marker % 2 == 0 else "female",
        region=value[:3],
    )


def _decode_pakistan(value: str) -> GovernmentIdEvidence:
    return _evidence(
        sex="male" if int(value[-1]) % 2 else "female",
        region=value[:5],
    )


def _decode_india_pan(value: str) -> GovernmentIdEvidence:
    issues = (
        ()
        if value[3] == "P"
        else ("PAN holder-type code must identify an individual person",)
    )
    return _evidence(
        name_components={
            "holder_type_code": value[3],
            "surname_initial": value[4],
        },
        issues=issues,
    )


def _decode_bangladesh_17(value: str) -> GovernmentIdEvidence:
    return _evidence(
        birth_years=(int(value[:4]),),
        region=value[4:11],
    )


def _decode_egypt(value: str) -> GovernmentIdEvidence:
    century = 1900 if value[0] == "2" else 2000
    return _evidence(
        birth_years=(century + int(value[1:3]),),
        month=int(value[3:5]),
        day=int(value[5:7]),
        sex="male" if int(value[12]) % 2 else "female",
        region=value[7:9],
    )


def _decode_south_africa(value: str) -> GovernmentIdEvidence:
    sequence = int(value[6:10])
    return _evidence(
        birth_years=_two_century_years(int(value[:2])),
        month=int(value[2:4]),
        day=int(value[4:6]),
        sex="male" if sequence >= 5000 else "female",
        citizenship="citizen" if value[10] == "0" else "permanent_resident",
    )


def _variant(
    name: str,
    pattern: str,
    hint: str,
    decoder: Decoder = _opaque,
) -> GovernmentIdVariant:
    return GovernmentIdVariant(
        name=name,
        pattern=re.compile(pattern),
        format_hint=hint,
        decoder=decoder,
    )


_RULES = (
    GovernmentIdRule("United States", (_variant("SSN", r"\d{9}", "SSN: exactly 9 digits"),)),
    GovernmentIdRule("Canada", (_variant("SIN", r"\d{9}", "SIN: exactly 9 digits"),)),
    GovernmentIdRule(
        "Mexico",
        (_variant(
            "CURP",
            r"[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d",
            "CURP: exactly 18 characters matching LLLL-YYMMDD-S-LLLLL-X-D; YYMMDD must match age, S is H for male or M for female, and the letter components must be compatible with the person's name and birth region",
            _decode_mexico,
        ),),
    ),
    GovernmentIdRule("Brazil", (_variant("CPF", r"\d{11}", "CPF: exactly 11 digits"),)),
    GovernmentIdRule("Colombia", (_variant("citizenship_id", r"\d{6,10}", "national identity card number: 6 to 10 digits"),)),
    GovernmentIdRule("United Kingdom", (_variant("NINO", r"[A-Z]{2}\d{6}[A-D]", "National Insurance number: exactly two letters, six digits, and one final letter from A through D"),)),
    GovernmentIdRule("Germany", (_variant("Personalausweis", r"[A-Z0-9]{9}", "Personalausweis document number: exactly 9 uppercase alphanumeric characters"),)),
    GovernmentIdRule(
        "France",
        (_variant("NIR", r"[12]\d{14}", "INSEE/NIR: exactly 15 digits; the first digit encodes sex, followed by YYMM birth information and a five-digit birth-region code", _decode_france),),
    ),
    GovernmentIdRule(
        "Poland",
        (_variant("PESEL", r"\d{11}", "PESEL: exactly 11 digits; encoded birth date and sex digit must match the profile", _decode_poland),),
    ),
    GovernmentIdRule(
        "China",
        (_variant("resident_identity_number", r"\d{17}[0-9X]", "resident identity number: exactly 18 characters with 6-digit address code, YYYYMMDD birth date, 3-digit sequence whose final digit is odd for male and even for female, then one digit or X", _decode_china),),
    ),
    GovernmentIdRule("Japan", (_variant("My Number", r"\d{12}", "My Number: exactly 12 digits"),)),
    GovernmentIdRule(
        "South Korea",
        (_variant("resident_registration_number", r"\d{13}", "resident registration number: exactly 13 digits; YYMMDD and the century/sex/citizenship marker must match the profile", _decode_south_korea),),
    ),
    GovernmentIdRule(
        "Indonesia",
        (_variant("NIK", r"\d{16}", "NIK: exactly 16 digits; six-digit region code followed by DDMMYY birth information, with 40 added to the day for female", _decode_indonesia),),
    ),
    GovernmentIdRule("Philippines", (_variant("PhilSys_number", r"\d{12}", "PhilSys number: exactly 12 digits"),)),
    GovernmentIdRule(
        "Vietnam",
        (_variant("citizen_identity_number", r"\d{12}", "citizen identity number: exactly 12 digits; three-digit province code, century/sex marker, and two-digit birth year must match the profile", _decode_vietnam),),
    ),
    GovernmentIdRule(
        "India",
        (
            _variant("Aadhaar", r"\d{12}", "Aadhaar: exactly 12 digits"),
            _variant("PAN", r"[A-Z]{5}\d{4}[A-Z]", "PAN: five letters, four digits, and one final letter; the fourth character must be P for an individual and the fifth character is the surname initial", _decode_india_pan),
        ),
    ),
    GovernmentIdRule(
        "Pakistan",
        (_variant("CNIC", r"\d{13}", "CNIC: exactly 13 digits; the first five digits are a region/family code and the last digit's parity encodes sex", _decode_pakistan),),
    ),
    GovernmentIdRule(
        "Bangladesh",
        (
            _variant("smart_NID", r"\d{10}", "smart national identity number: exactly 10 digits"),
            _variant("legacy_NID_13", r"\d{13}", "legacy national identity number: exactly 13 digits"),
            _variant("legacy_NID_17", r"\d{17}", "legacy national identity number: exactly 17 digits beginning with the four-digit birth year, followed by district/locality codes and a serial number", _decode_bangladesh_17),
        ),
    ),
    GovernmentIdRule(
        "Egypt",
        (_variant("national_identity_number", r"[23]\d{13}", "national identity number: exactly 14 digits with century marker plus YYMMDD birth date, two-digit governorate code, and a second-to-last sex digit that is odd for male and even for female", _decode_egypt),),
    ),
    GovernmentIdRule("Turkey", (_variant("national_identity_number", r"[1-9]\d{10}", "national identity number: exactly 11 digits and the first digit must be non-zero"),)),
    GovernmentIdRule("Iran", (_variant("national_identity_number", r"\d{10}", "national identity number: exactly 10 digits"),)),
    GovernmentIdRule("Nigeria", (_variant("NIN", r"\d{11}", "NIN: exactly 11 digits"),)),
    GovernmentIdRule("Ethiopia", (_variant("Fayda", r"\d{12}", "Fayda identification number: exactly 12 digits"),)),
    GovernmentIdRule(
        "South Africa",
        (_variant("identity_number", r"\d{13}", "identity number: exactly 13 digits; YYMMDD, four-digit sex sequence, and citizenship/residency marker must match the profile", _decode_south_africa),),
    ),
    GovernmentIdRule("Kenya", (_variant("national_identity_number", r"\d{7,8}", "national identity number: 7 or 8 digits"),)),
    GovernmentIdRule("Australia", (_variant("TFN", r"\d{9}", "tax file number: exactly 9 digits"),)),
)


GOVERNMENT_ID_RULES: Mapping[str, GovernmentIdRule] = MappingProxyType(
    {rule.country: rule for rule in _RULES}
)


def _random_digits(rng: Any, length: int) -> str:
    return "".join(rng.choice(string.digits) for _ in range(length))


def _random_letters(rng: Any, length: int) -> str:
    return "".join(rng.choice(string.ascii_uppercase) for _ in range(length))


def _random_alnum(rng: Any, length: int) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(rng.choice(alphabet) for _ in range(length))


def _normalized_name_tokens(name: str) -> list[str]:
    ascii_name = unicodedata.normalize("NFKD", str(name)).encode(
        "ascii", "ignore"
    ).decode("ascii")
    return re.findall(r"[A-Z]+", ascii_name.upper())


def _internal_letter(value: str, *, vowel: bool) -> str:
    alphabet = "AEIOU" if vowel else "BCDFGHJKLMNPQRSTVWXYZ"
    return next((letter for letter in value[1:] if letter in alphabet), "X")


def _synthetic_birth_date(
    possible_birth_years: Sequence[int], rng: Any
) -> tuple[int, int, int]:
    years = tuple(int(value) for value in possible_birth_years)
    if not years:
        raise ValueError("At least one possible birth year is required")
    year = rng.choice(years)
    month = rng.randint(1, 12)
    day = rng.randint(1, monthrange(year, month)[1])
    return year, month, day


def _matched_variant(country: str, normalized: str) -> str | None:
    rule = GOVERNMENT_ID_RULES[country]
    return next(
        (
            variant.name
            for variant in rule.variants
            if variant.pattern.fullmatch(normalized)
        ),
        None,
    )


def _different_digit(value: str, rng: Any) -> str:
    return rng.choice(string.digits.replace(value, ""))


def repair_government_id(
    *,
    country: str,
    previous_value: str,
    validation_issues: Sequence[str],
    possible_birth_years: Sequence[int],
    expected_sex: str,
    name: str,
    birth_location: str,
    rng: Any,
) -> str:
    """Repair only identifier components named by deterministic/LLM feedback.

    A parseable prior identifier is edited in place: unaffected region, serial,
    name, and document-type components remain unchanged.  A fresh registered
    baseline is constructed only when the prior value has no recognized format.
    """

    if country not in GOVERNMENT_ID_RULES:
        raise ValueError(f"No government-ID synthesizer is registered for {country}")
    if expected_sex not in {"male", "female"}:
        raise ValueError(f"Unsupported sex for government-ID synthesis: {expected_sex}")
    previous = re.sub(
        r"[^A-Z0-9]", "", str(previous_value).strip().upper()
    )
    variant = _matched_variant(country, previous)
    feedback = " ".join(str(issue).casefold() for issue in validation_issues)
    rebuild = variant is None or any(
        marker in feedback
        for marker in (
            "must use a configured",
            "wrong_document_type",
            "citizenship_mismatch",
            "internally_inconsistent",
            "other_clear_government_id_contradiction",
            "placeholder",
        )
    )
    fix_birth = rebuild or any(
        marker in feedback for marker in ("birth", "date", "month", "year")
    )
    fix_sex = rebuild or any(
        marker in feedback for marker in ("sex", "gender", "parity")
    )
    fix_region = rebuild or any(
        marker in feedback for marker in ("region", "governorate", "province")
    )
    fix_name = rebuild or "name_component" in feedback or "surname" in feedback
    fix_citizenship = rebuild or "citizenship" in feedback
    fix_duplicate = "duplicate" in feedback
    year, month, day = _synthetic_birth_date(possible_birth_years, rng)

    opaque_lengths = {
        "United States": (9, 9),
        "Canada": (9, 9),
        "Brazil": (11, 11),
        "Colombia": (6, 10),
        "Japan": (12, 12),
        "Philippines": (12, 12),
        "Iran": (10, 10),
        "Nigeria": (11, 11),
        "Ethiopia": (12, 12),
        "Kenya": (7, 8),
        "Australia": (9, 9),
    }
    if country in opaque_lengths:
        if variant is not None and not rebuild:
            if fix_duplicate:
                return previous[:-1] + _different_digit(previous[-1], rng)
            return previous
        low, high = opaque_lengths[country]
        return _random_digits(rng, rng.randint(low, high))
    if country == "United Kingdom":
        if variant is not None and not rebuild:
            return (
                previous[:-2]
                + _different_digit(previous[-2], rng)
                + previous[-1]
                if fix_duplicate
                else previous
            )
        return _random_letters(rng, 2) + _random_digits(rng, 6) + rng.choice("ABCD")
    if country == "Germany":
        if variant is not None and not rebuild:
            if fix_duplicate:
                replacement = rng.choice(
                    (string.ascii_uppercase + string.digits).replace(previous[-1], "")
                )
                return previous[:-1] + replacement
            return previous
        return _random_alnum(rng, 9)
    if country == "Turkey":
        if variant is not None and not rebuild:
            return (
                previous[:-1] + _different_digit(previous[-1], rng)
                if fix_duplicate
                else previous
            )
        return rng.choice("123456789") + _random_digits(rng, 10)

    if country == "Mexico":
        tokens = _normalized_name_tokens(name)
        given = tokens[0] if tokens else "X"
        paternal = tokens[-1] if len(tokens) > 1 else given
        maternal = tokens[-2] if len(tokens) > 2 else "X"
        prefix = (
            paternal[0]
            + _internal_letter(paternal, vowel=True)
            + maternal[0]
            + given[0]
        )
        region = "NE" if not str(birth_location).endswith(", Mexico") else "DF"
        internals = (
            _internal_letter(paternal, vowel=False)
            + _internal_letter(maternal, vowel=False)
            + _internal_letter(given, vowel=False)
        )
        baseline = (
            prefix
            + f"{year % 100:02d}{month:02d}{day:02d}"
            + ("H" if expected_sex == "male" else "M")
            + region
            + internals
            + _random_alnum(rng, 1)
            + _random_digits(rng, 1)
        )
        if variant != "CURP" or rebuild:
            return baseline
        repaired = list(previous)
        if fix_name:
            repaired[:4] = prefix
            repaired[13:16] = internals
        if fix_birth:
            repaired[4:10] = f"{year % 100:02d}{month:02d}{day:02d}"
        if fix_sex:
            repaired[10] = "H" if expected_sex == "male" else "M"
        if fix_region:
            repaired[11:13] = region
        if fix_duplicate:
            repaired[17] = _different_digit(repaired[17], rng)
        return "".join(repaired)
    if country == "France":
        region = previous[5:10] if variant == "NIR" else "75001"
        suffix = previous[10:15] if variant == "NIR" else _random_digits(rng, 5)
        baseline = (
            ("1" if expected_sex == "male" else "2")
            + f"{year % 100:02d}{month:02d}"
            + region
            + suffix
        )
        if variant != "NIR" or rebuild:
            return baseline
        repaired = list(previous)
        if fix_sex:
            repaired[0] = "1" if expected_sex == "male" else "2"
        if fix_birth:
            repaired[1:5] = f"{year % 100:02d}{month:02d}"
        if fix_region:
            repaired[5:10] = (
                "99999" if not str(birth_location).endswith(", France") else "75001"
            )
        if fix_duplicate:
            repaired[-1] = _different_digit(repaired[-1], rng)
        return "".join(repaired)
    if country == "Poland":
        encoded_month = month + (20 if year >= 2000 else 0)
        sex_digit = rng.choice("13579" if expected_sex == "male" else "02468")
        baseline = (
            f"{year % 100:02d}{encoded_month:02d}{day:02d}"
            + _random_digits(rng, 3)
            + sex_digit
            + _random_digits(rng, 1)
        )
        if variant != "PESEL" or rebuild:
            return baseline
        repaired = list(previous)
        if fix_birth:
            repaired[:6] = f"{year % 100:02d}{encoded_month:02d}{day:02d}"
        if fix_sex:
            repaired[9] = sex_digit
        if fix_duplicate:
            repaired[-1] = _different_digit(repaired[-1], rng)
        return "".join(repaired)
    if country == "China":
        region = previous[:6] if variant == "resident_identity_number" else "110101"
        sex_digit = rng.choice("13579" if expected_sex == "male" else "02468")
        baseline = (
            region
            + f"{year:04d}{month:02d}{day:02d}"
            + _random_digits(rng, 2)
            + sex_digit
            + rng.choice(string.digits + "X")
        )
        if variant != "resident_identity_number" or rebuild:
            return baseline
        repaired = list(previous)
        if fix_region:
            repaired[:6] = "110101"
        if fix_birth:
            repaired[6:14] = f"{year:04d}{month:02d}{day:02d}"
        if fix_sex:
            repaired[16] = sex_digit
        if fix_duplicate:
            replacement_pool = (string.digits + "X").replace(repaired[-1], "")
            repaired[-1] = rng.choice(replacement_pool)
        return "".join(repaired)
    if country == "South Korea":
        if year < 2000:
            marker = "1" if expected_sex == "male" else "2"
        else:
            marker = "3" if expected_sex == "male" else "4"
        suffix = previous[7:] if variant == "resident_registration_number" else _random_digits(rng, 6)
        baseline = f"{year % 100:02d}{month:02d}{day:02d}" + marker + suffix
        if variant != "resident_registration_number" or rebuild:
            return baseline
        repaired = list(previous)
        if fix_birth:
            repaired[:6] = f"{year % 100:02d}{month:02d}{day:02d}"
        if fix_birth or fix_sex or fix_citizenship:
            repaired[6] = marker
        if fix_duplicate:
            repaired[-1] = _different_digit(repaired[-1], rng)
        return "".join(repaired)
    if country == "Indonesia":
        region = previous[:6] if variant == "NIK" else "317301"
        encoded_day = day + (40 if expected_sex == "female" else 0)
        serial = previous[12:] if variant == "NIK" else _random_digits(rng, 4)
        baseline = region + f"{encoded_day:02d}{month:02d}{year % 100:02d}" + serial
        if variant != "NIK" or rebuild:
            return baseline
        repaired = list(previous)
        if fix_region:
            repaired[:6] = "317301"
        if fix_birth or fix_sex:
            repaired[6:12] = f"{encoded_day:02d}{month:02d}{year % 100:02d}"
        if fix_duplicate:
            repaired[-1] = _different_digit(repaired[-1], rng)
        return "".join(repaired)
    if country == "Vietnam":
        province = previous[:3] if variant == "citizen_identity_number" else "001"
        century = (year // 100) - 19
        marker = str(century * 2 + (1 if expected_sex == "female" else 0))
        serial = previous[6:] if variant == "citizen_identity_number" else _random_digits(rng, 6)
        baseline = province + marker + f"{year % 100:02d}" + serial
        if variant != "citizen_identity_number" or rebuild:
            return baseline
        repaired = list(previous)
        if fix_region:
            repaired[:3] = "001"
        if fix_birth or fix_sex:
            repaired[3] = marker
            repaired[4:6] = f"{year % 100:02d}"
        if fix_duplicate:
            repaired[-1] = _different_digit(repaired[-1], rng)
        return "".join(repaired)
    if country == "India":
        if variant == "PAN":
            tokens = _normalized_name_tokens(name)
            surname_initial = (tokens[-1] if tokens else "X")[0]
            baseline = (
                _random_letters(rng, 3)
                + "P"
                + surname_initial
                + _random_digits(rng, 4)
                + _random_letters(rng, 1)
            )
            if rebuild:
                return baseline
            repaired = list(previous)
            if fix_name:
                repaired[4] = surname_initial
            if repaired[3] != "P":
                repaired[3] = "P"
            if fix_duplicate:
                repaired[8] = _different_digit(repaired[8], rng)
            return "".join(repaired)
        if variant == "Aadhaar" and not rebuild:
            return (
                previous[:-1] + _different_digit(previous[-1], rng)
                if fix_duplicate
                else previous
            )
        return _random_digits(rng, 12)
    if country == "Pakistan":
        prefix = previous[:12] if variant == "CNIC" else _random_digits(rng, 12)
        sex_digit = rng.choice("13579" if expected_sex == "male" else "02468")
        baseline = prefix + sex_digit
        if variant != "CNIC" or rebuild:
            return baseline
        repaired = list(previous)
        if fix_region:
            repaired[:5] = "35202"
        if fix_sex:
            repaired[-1] = sex_digit
        if fix_duplicate:
            repaired[-2] = _different_digit(repaired[-2], rng)
        return "".join(repaired)
    if country == "Bangladesh":
        if variant == "legacy_NID_17":
            return f"{year:04d}" + previous[4:]
        if variant == "legacy_NID_13":
            return _random_digits(rng, 13)
        return _random_digits(rng, 10)
    if country == "Egypt":
        region = previous[7:9] if variant == "national_identity_number" else "01"
        sex_digit = rng.choice("13579" if expected_sex == "male" else "02468")
        baseline = (
            ("2" if year < 2000 else "3")
            + f"{year % 100:02d}{month:02d}{day:02d}"
            + region
            + _random_digits(rng, 3)
            + sex_digit
            + _random_digits(rng, 1)
        )
        if variant != "national_identity_number" or rebuild:
            return baseline
        repaired = list(previous)
        if fix_birth:
            repaired[:7] = (
                ("2" if year < 2000 else "3")
                + f"{year % 100:02d}{month:02d}{day:02d}"
            )
        if fix_region:
            repaired[7:9] = "01"
        if fix_sex:
            repaired[12] = sex_digit
        if fix_duplicate:
            repaired[-1] = _different_digit(repaired[-1], rng)
        return "".join(repaired)
    if country == "South Africa":
        sequence = rng.randint(5000, 9999) if expected_sex == "male" else rng.randint(0, 4999)
        baseline = (
            f"{year % 100:02d}{month:02d}{day:02d}{sequence:04d}0"
            + _random_digits(rng, 2)
        )
        if variant != "identity_number" or rebuild:
            return baseline
        repaired = list(previous)
        if fix_birth:
            repaired[:6] = f"{year % 100:02d}{month:02d}{day:02d}"
        if fix_sex:
            repaired[6:10] = f"{sequence:04d}"
        if fix_citizenship:
            repaired[10] = "0"
        if fix_duplicate:
            repaired[-1] = _different_digit(repaired[-1], rng)
        return "".join(repaired)
    raise ValueError(f"Government-ID synthesis is incomplete for {country}")


def government_id_format_hints() -> dict[str, str]:
    return {
        country: rule.format_hint
        for country, rule in GOVERNMENT_ID_RULES.items()
    }


def inspect_government_id(
    *,
    country: str,
    raw_value: str,
    possible_birth_years: Sequence[int],
    expected_sex: str,
) -> GovernmentIdInspection:
    """Normalize, identify, decode, and compare one synthetic identifier."""

    normalized = re.sub(r"[^A-Z0-9]", "", str(raw_value).strip().upper())
    rule = GOVERNMENT_ID_RULES.get(country)
    if rule is None:
        return GovernmentIdInspection(
            country=country,
            document_type=None,
            normalized=normalized,
            format_hint="unsupported country",
            decoded_components=MappingProxyType({}),
            issues=(f"no government_id rule is registered for {country}",),
        )

    variant = next(
        (
            candidate
            for candidate in rule.variants
            if candidate.pattern.fullmatch(normalized)
        ),
        None,
    )
    if variant is None:
        return GovernmentIdInspection(
            country=country,
            document_type=None,
            normalized=normalized,
            format_hint=rule.format_hint,
            decoded_components=MappingProxyType({}),
            issues=(
                f"government_id must use a configured {country} format: "
                f"{rule.format_hint}",
            ),
        )

    evidence = variant.decoder(normalized)
    issues = list(evidence.intrinsic_issues)
    expected_years = {int(value) for value in possible_birth_years}
    if evidence.birth_years and expected_years.isdisjoint(evidence.birth_years):
        issues.append("government_id encoded birth year must match age")
    if evidence.birth_month is not None:
        candidate_years = evidence.birth_years or tuple(expected_years)
        if evidence.birth_day is None:
            if not 1 <= evidence.birth_month <= 12:
                issues.append(
                    "government_id must contain a valid encoded birth month"
                )
        elif not any(
            _valid_date(year, evidence.birth_month, evidence.birth_day)
            for year in candidate_years
        ):
            issues.append(
                "government_id must contain a valid encoded birth date"
            )
    if evidence.sex is not None and evidence.sex != expected_sex:
        issues.append("government_id encoded sex must match sex")
    if (
        re.search(r"123456789|987654321", normalized)
        or len(set(normalized)) == 1
    ):
        issues.append("government_id must not use an obvious placeholder sequence")

    return GovernmentIdInspection(
        country=country,
        document_type=variant.name,
        normalized=normalized,
        format_hint=rule.format_hint,
        decoded_components=MappingProxyType(evidence.as_prompt_dict()),
        issues=tuple(dict.fromkeys(issues)),
    )


def _valid_date(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True
