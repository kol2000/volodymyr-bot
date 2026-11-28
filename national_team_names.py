"""Country/territory names and verified football caption spellings."""
import json
import logging
import re
from pathlib import Path
NATIONAL_NAMES = {
 'англия': ('İngiltərə','England'), 'хорватия': ('Xorvatiya','Croatia'),
 'италия': ('İtaliya','Italy'), 'франция': ('Fransa','France'),
 'испания': ('İspaniya','Spain'), 'чехия': ('Çexiya','Czechia'),
 'турция': ('Türkiyə','Türkiye','Turkey'), 'бельгия': ('Belçika','Belgium'),
 'россия': ('Rusiya','Russia'), 'узбекистан': ('Özbəkistan','Uzbekistan'),
 'беларусь': ('Belarus','Belarusiya'), 'сан-марино': ('San Marino',),
 'германия': ('Almaniya','Germany'), 'португалия': ('Portuqaliya','Portugal'),
 'нидерланды': ('Niderland','Niderlandlar','Netherlands'), 'польша': ('Polşa','Poland'),
 'швейцария': ('İsveçrə','Switzerland'), 'швеция': ('İsveç','Sweden'),
 'норвегия': ('Norveç','Norway'), 'дания': ('Danimarka','Denmark'),
 'сербия': ('Serbiya','Serbia'), 'австрия': ('Avstriya','Austria'),
 'азербайджан': ('Azərbaycan','Azerbaijan'), 'грузия': ('Gürcüstan','Georgia'),
 'украина': ('Ukrayna','Ukraine'), 'венгрия': ('Macarıstan','Hungary'),
 'шотландия': ('Şotlandiya','Scotland'), 'ирландия': ('İrlandiya','Ireland'),
 'северная ирландия': ('Şimali İrlandiya','Northern Ireland'),
 'северная македония': ('Şimali Makedoniya','North Macedonia'),
 'бразилия': ('Braziliya','Brazil'), 'аргентина': ('Argentina',),

 'албания': ('Albaniya','Albania'), 'андорра': ('Andorra',),
 'армения': ('Ermənistan','Armenia'), 'босния и герцеговина': ('Bosniya və Herseqovina','Bosnia and Herzegovina'),
 'болгария': ('Bolqarıstan','Bulgaria'), 'кипр': ('Kipr','Cyprus'),
 'эстония': ('Estoniya','Estonia'), 'финляндия': ('Finlandiya','Finland'),
 'гибралтар': ('Cəbəllütariq','Gibraltar'), 'греция': ('Yunanıstan','Greece'),
 'исландия': ('İslandiya','Iceland'), 'израиль': ('İsrail','Israel'),
 'казахстан': ('Qazaxıstan','Kazakhstan'), 'косово': ('Kosovo',),
 'латвия': ('Latviya','Latvia'), 'лихтенштейн': ('Lixtenşteyn','Liechtenstein'),
 'литва': ('Litva','Lithuania'), 'люксембург': ('Lüksemburq','Luxembourg'),
 'мальта': ('Malta',), 'молдова': ('Moldova',),
 'черногория': ('Monteneqro','Montenegro'), 'румыния': ('Rumıniya','Romania'),
 'словакия': ('Slovakiya','Slovakia'), 'словения': ('Sloveniya','Slovenia'),
 'уэльс': ('Uels','Wales'), 'фарерские острова': ('Farer adaları','Faroe Islands'),
 'уругвай': ('Uruqvay','Uruguay'), 'колумбия': ('Kolumbiya','Colombia'),
 'эквадор': ('Ekvador','Ecuador'), 'парагвай': ('Paraqvay','Paraguay'),
 'чили': ('Çili','Chile'), 'перу': ('Peru',), 'венесуэла': ('Venesuela','Venezuela'),
 'боливия': ('Boliviya','Bolivia'), 'мексика': ('Meksika','Mexico'),
 'сша': ('ABŞ','USA','United States'), 'канада': ('Kanada','Canada'),
 'япония': ('Yaponiya','Japan'), 'южная корея': ('Cənubi Koreya','South Korea'),
 'иран': ('İran','Iran'), 'австралия': ('Avstraliya','Australia'),
 'саудовская аравия': ('Səudiyyə Ərəbistanı','Saudi Arabia'),
 'катар': ('Qətər','Qatar'), 'марокко': ('Mərakeş','Morocco'),
 'египет': ('Misir','Egypt'), 'сенегал': ('Seneqal','Senegal'),
 'нигерия': ('Nigeriya','Nigeria'), 'алжир': ('Əlcəzair','Algeria'),
 'тунис': ('Tunis','Tunisia'), 'камерун': ('Kamerun','Cameroon'),
 'гана': ('Qana','Ghana'), 'кот-д’ивуар': ('Kot-d’İvuar','Kot-d İvuar','Ivory Coast'),
}

# Preserve the hand-verified football names if the larger offline file is absent.
try:
    _records = json.loads(Path(__file__).with_name('national_team_names_data.json').read_text(encoding='utf-8'))['teams']
    for _row in _records:
        _key = _row['ru'].casefold()
        NATIONAL_NAMES[_key] = tuple(dict.fromkeys((*NATIONAL_NAMES.get(_key, ()), *_row['aliases'])))
except (OSError, ValueError, KeyError, TypeError):
    logging.getLogger('volodymyr').warning('Расширенная база сборных недоступна; используется резервный словарь')

_owners = {}
for _key, _values in NATIONAL_NAMES.items():
    for _value in (_key, *_values):
        if re.search('[а-яё]', _value, re.I):
            _owners.setdefault(_value.casefold(), set()).add(_key)
NATIONAL_CANONICAL = {name: next(iter(keys)) for name, keys in _owners.items() if len(keys) == 1}
