"""Alias data for job matching. Data only. Every alias is a raw string that `tokenize` turns into a token tuple.

Canonical keys resolve from their own text without being listed here, except those marked WHOLE_ITEM_ONLY, which are listed so the
marking refers to a real alias. WHOLE_ITEM_ONLY aliases are ordinary words: they match only when they are the entire input item.
"""

SKILL_ALIASES: dict[str, str] = {
    # JavaScript and TypeScript
    "js": "javascript", "जावास्क्रिप्ट": "javascript",
    "ts": "typescript", "टाइपस्क्रिप्ट": "typescript",
    "reactjs": "react", "react.js": "react", "react js": "react", "रिएक्ट": "react",
    "nodejs": "node.js", "node js": "node.js", "node": "node.js", "नोड जेएस": "node.js",
    "vuejs": "vue", "vue.js": "vue",
    "react-native": "react native", "reactnative": "react native", "रिएक्ट नेटिव": "react native",
    # Languages
    "python3": "python", "python 3": "python", "पाइथन": "python", "पायथन": "python", "पायथॉन": "python",
    "जावा": "java",
    "कोटलिन": "kotlin",
    "golang": "go", "go": "go",
    "swift": "swift",
    "c sharp": "c#", "csharp": "c#",
    "cpp": "c++", "c plus plus": "c++",
    "dotnet": ".net", "dot net": ".net", ".net core": ".net", "asp.net": ".net", "asp.net core": ".net",
    "html5": "html", "एचटीएमएल": "html",
    "css3": "css", "सीएसएस": "css",
    "एसक्यूएल": "sql",
    # Data stores
    "postgres": "postgresql", "postgre sql": "postgresql",
    "mongo": "mongodb", "mongo db": "mongodb",
    # Frameworks
    "springboot": "spring boot", "spring": "spring boot",
    "rest api": "rest apis", "restful api": "rest apis", "restful apis": "rest apis", "rest": "rest apis",
    "फ्लटर": "flutter",
    "एंड्रॉइड": "android", "एंड्रॉयड": "android",
    # Cloud and tooling
    "k8s": "kubernetes", "कुबेरनेट्स": "kubernetes",
    "amazon web services": "aws", "एडब्ल्यूएस": "aws",
    "microsoft azure": "azure",
    "cicd": "ci/cd", "ci cd": "ci/cd", "ci-cd": "ci/cd",
    "डॉकर": "docker",
    "लिनक्स": "linux",
    "गिट": "git",
    # Analysis
    "powerbi": "power bi", "microsoft power bi": "power bi", "ms power bi": "power bi", "पावर बीआई": "power bi",
    "ms excel": "excel", "microsoft excel": "excel", "excel": "excel", "एक्सेल": "excel",
    "टैब्लो": "tableau",
    # Testing and design
    "selenium webdriver": "selenium", "सेलेनियम": "selenium",
    "manual qa": "manual testing", "मैन्युअल टेस्टिंग": "manual testing", "मैनुअल टेस्टिंग": "manual testing",
    "फिग्मा": "figma",
}

_BACKEND = ("backend-developer",)
_FRONTEND = ("frontend-developer",)
_FULL_STACK = ("full-stack-developer",)
_MOBILE = ("mobile-developer",)
_DATA_ANALYST = ("data-analyst",)
_DATA_ENGINEER = ("data-engineer",)
_DEVOPS = ("devops-engineer",)
_QA = ("qa-engineer",)
_DESIGNER = ("ui-ux-designer",)
_WEB = ("frontend-developer", "full-stack-developer")
_SOFTWARE = ("backend-developer", "full-stack-developer")

ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "backend": _BACKEND, "back end": _BACKEND, "back end developer": _BACKEND, "backend engineer": _BACKEND,
    "python developer": _BACKEND, "java developer": _BACKEND, "node.js developer": _BACKEND, "nodejs developer": _BACKEND,
    "बैकएंड डेवलपर": _BACKEND, "बैक एंड डेवलपर": _BACKEND,
    "frontend": _FRONTEND, "front end": _FRONTEND, "front end developer": _FRONTEND, "frontend engineer": _FRONTEND,
    "ui developer": _FRONTEND, "react developer": _FRONTEND,
    "फ्रंटएंड डेवलपर": _FRONTEND, "फ्रंट एंड डेवलपर": _FRONTEND,
    "web developer": _WEB, "वेब डेवलपर": _WEB,
    "fullstack": _FULL_STACK, "full stack": _FULL_STACK, "fullstack developer": _FULL_STACK, "full stack engineer": _FULL_STACK,
    "full stack web developer": _FULL_STACK, "फुल स्टैक डेवलपर": _FULL_STACK, "फुलस्टैक डेवलपर": _FULL_STACK,
    "software engineer": _SOFTWARE, "software developer": _SOFTWARE, "sde": _SOFTWARE, "programmer": _SOFTWARE,
    "सॉफ्टवेयर इंजीनियर": _SOFTWARE, "सॉफ्टवेयर डेवलपर": _SOFTWARE,
    "android developer": _MOBILE, "ios developer": _MOBILE, "mobile app developer": _MOBILE, "flutter developer": _MOBILE,
    "react native developer": _MOBILE, "मोबाइल डेवलपर": _MOBILE, "एंड्रॉइड डेवलपर": _MOBILE,
    "business analyst": _DATA_ANALYST, "bi analyst": _DATA_ANALYST, "डेटा एनालिस्ट": _DATA_ANALYST, "डाटा एनालिस्ट": _DATA_ANALYST,
    "big data engineer": _DATA_ENGINEER, "etl developer": _DATA_ENGINEER, "डेटा इंजीनियर": _DATA_ENGINEER,
    "डाटा इंजीनियर": _DATA_ENGINEER,
    "devops": _DEVOPS, "sre": _DEVOPS, "site reliability engineer": _DEVOPS, "cloud engineer": _DEVOPS,
    "डेवऑप्स इंजीनियर": _DEVOPS, "डेवऑप्स": _DEVOPS,
    "qa": _QA, "tester": _QA, "test engineer": _QA, "software tester": _QA, "sdet": _QA, "automation engineer": _QA,
    "automation tester": _QA, "quality assurance engineer": _QA, "क्यूए इंजीनियर": _QA, "टेस्टर": _QA,
    "सॉफ्टवेयर टेस्टर": _QA,
    "ux designer": _DESIGNER, "ui designer": _DESIGNER, "ui/ux designer": _DESIGNER, "ui ux designer": _DESIGNER,
    "product designer": _DESIGNER, "यूआई यूएक्स डिजाइनर": _DESIGNER, "यूआई/यूएक्स डिजाइनर": _DESIGNER,
    "यूआई यूएक्स डिज़ाइनर": _DESIGNER, "यूआई/यूएक्स डिज़ाइनर": _DESIGNER,
}

_DELHI = ("delhi",)
_NOIDA = ("noida",)
_GURUGRAM = ("gurugram",)
_BENGALURU = ("bengaluru",)
_HYDERABAD = ("hyderabad",)
_PUNE = ("pune",)
_MUMBAI = ("mumbai",)
_CHENNAI = ("chennai",)
_KOLKATA = ("kolkata",)
_JAIPUR = ("jaipur",)
_NCR = ("delhi", "noida", "gurugram")

CITY_ALIASES: dict[str, tuple[str, ...]] = {
    "new delhi": _DELHI, "dilli": _DELHI, "दिल्ली": _DELHI, "नई दिल्ली": _DELHI,
    "नोएडा": _NOIDA, "नोयडा": _NOIDA,
    "gurgaon": _GURUGRAM, "गुरुग्राम": _GURUGRAM, "गुड़गांव": _GURUGRAM, "गुडगांव": _GURUGRAM,
    "गुड़गाँव": _GURUGRAM, "गुरगाँव": _GURUGRAM, "गुरगांव": _GURUGRAM,
    "ncr": _NCR, "delhi ncr": _NCR, "दिल्ली एनसीआर": _NCR, "एनसीआर": _NCR,
    "bangalore": _BENGALURU, "banglore": _BENGALURU, "bengalooru": _BENGALURU, "बेंगलुरु": _BENGALURU, "बेंगलुरू": _BENGALURU,
    "बैंगलोर": _BENGALURU, "बंगलौर": _BENGALURU, "बैंगलुरू": _BENGALURU,
    "haidarabad": _HYDERABAD, "हैदराबाद": _HYDERABAD,
    "poona": _PUNE, "puna": _PUNE, "पुणे": _PUNE, "पूना": _PUNE,
    "bombay": _MUMBAI, "bambai": _MUMBAI, "मुंबई": _MUMBAI, "बॉम्बे": _MUMBAI, "बंबई": _MUMBAI,
    "madras": _CHENNAI, "चेन्नई": _CHENNAI, "मद्रास": _CHENNAI,
    "calcutta": _KOLKATA, "kalkatta": _KOLKATA, "कोलकाता": _KOLKATA, "कलकत्ता": _KOLKATA,
    "जयपुर": _JAIPUR,
}

_FRESHER = ("fresher",)
_JUNIOR = ("junior",)
_MID = ("mid",)
_SENIOR = ("senior",)

LEVEL_ALIASES: dict[str, tuple[str, ...]] = {
    "fresher": _FRESHER, "freshers": _FRESHER, "fresh graduate": _FRESHER, "recent graduate": _FRESHER, "graduate": _FRESHER,
    "entry level": _FRESHER, "entry-level": _FRESHER, "beginner": _FRESHER, "trainee": _FRESHER, "intern": _FRESHER,
    "internship": _FRESHER, "student": _FRESHER, "no experience": _FRESHER,
    "फ्रेशर": _FRESHER, "फ्रेशर्स": _FRESHER, "एंट्री लेवल": _FRESHER, "बिगिनर": _FRESHER, "ट्रेनी": _FRESHER, "इंटर्न": _FRESHER,
    "junior": _JUNIOR, "jr": _JUNIOR, "junior level": _JUNIOR, "जूनियर": _JUNIOR,
    "mid": _MID, "mid level": _MID, "mid-level": _MID, "intermediate": _MID, "मिड": _MID, "मिड लेवल": _MID,
    "senior": _SENIOR, "sr": _SENIOR, "senior level": _SENIOR, "सीनियर": _SENIOR,
}

# One set for all tables; each index uses only the entries that are aliases in its own table.
WHOLE_ITEM_ONLY: frozenset[str] = frozenset({"node", "go", "rest", "excel", "एक्सेल", "spring", "swift", "graduate", "student"})
