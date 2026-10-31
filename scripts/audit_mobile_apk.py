"""Read-only release ZIP audit; never signs or accesses a signing key."""
import hashlib
import re
import zipfile
import json
import argparse
from pathlib import Path

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--allow-native-change',action='store_true',help='Explicit native-fix audit; report DEX delta instead of requiring UI-only release')
args=parser.parse_args()
apk=root/'mobile/android/app/build/outputs/apk/release/app-release-unsigned.apk'
assets=root/'mobile/dist'
metadata=json.loads(apk.with_name('output-metadata.json').read_text(encoding='utf-8'))
build=(root/'mobile/android/app/build.gradle').read_text(encoding='utf-8')
expected_code=int(re.search(r'versionCode\s+(\d+)',build).group(1))
expected_name=re.search(r'versionName\s+"([^"]+)"',build).group(1)
element=metadata['elements'][0]
if (metadata['applicationId']!='xyz.cerberusgamelabs.iam.messenger' or metadata['variantName']!='release'
        or element['versionCode']!=expected_code or element['versionName']!=expected_name):
    raise SystemExit('Release metadata does not match current source version')
with zipfile.ZipFile(apk) as archive:
    checked=0
    for path in assets.iterdir():
        if path.is_file():
            if archive.read('assets/public/'+path.name)!=path.read_bytes():
                raise SystemExit('Packaged asset mismatch: '+path.name)
            checked+=1
    for info in archive.infolist():
        if info.file_size>100_000_000:raise SystemExit('Unexpected oversized entry')
        if re.search(r'(?:^|/)(?:\.env(?:\.[^/]*)?|webconnect(?:-state)?\.json|.*\.(?:p12|pfx|jks|keystore|sqlite3))$',info.filename,re.I):
            raise SystemExit('Unexpected private-state filename in release')
        if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',archive.read(info)):
            raise SystemExit('Private-key marker found; release rejected')
    config=json.loads(archive.read('assets/capacitor.config.json'))
    if config.get('server',{}).get('cleartext') is not False or config.get('android',{}).get('webContentsDebuggingEnabled') is not False:
        raise SystemExit('Unsafe packaged web configuration')
    old=root/'mobile/releases/iam-messenger-0.9.9-pilot.apk'
    if expected_name=='0.9.9b-pilot' and old.exists():
        with zipfile.ZipFile(old) as previous:
            dex=[name for name in archive.namelist() if re.fullmatch(r'classes\d*\.dex',name)]
            changed=[name for name in dex if archive.read(name)!=previous.read(name)]
            if changed and not args.allow_native_change:
                raise SystemExit('Unexpected native-code change from 0.9.9; investigate before signing')
            print('Native DEX files:',len(dex),'changed:',len(changed))
print('Release:',expected_name,'code',expected_code)
print('Matching web assets:',checked)
print('No private-key markers found')
print('APK bytes:',apk.stat().st_size)
print('SHA256:',hashlib.file_digest(apk.open('rb'),'sha256').hexdigest() if hasattr(hashlib,'file_digest') else hashlib.sha256(apk.read_bytes()).hexdigest())
