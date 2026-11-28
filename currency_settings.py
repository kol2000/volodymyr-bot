"""Validated currency recognition preferences; price sources remain fixed."""
import re

CURRENCIES = {'RUB':'Российский рубль', 'USD':'Доллар США', 'EUR':'Евро',
              'BYN':'Белорусский рубль', 'UAH':'Гривна', 'KZT':'Тенге',
              'BTC':'Bitcoin', 'ETH':'Ethereum', 'BNB':'BNB', 'SOL':'Solana',
              'TON':'Toncoin', 'DOGE':'Dogecoin', 'USDT':'Tether', 'USDC':'USD Coin'}
DEFAULT_CURRENCIES = {code: {'enabled': True, 'aliases': []} for code in CURRENCIES}


def validate_currencies(value):
    from chat_services import amounts_in
    if not isinstance(value, dict) or set(value) != set(CURRENCIES):
        raise ValueError('Передай настройки всех поддерживаемых валют')
    result, owners = {}, {}
    for code, row in value.items():
        if (not isinstance(row, dict) or set(row) != {'enabled', 'aliases'}
                or type(row['enabled']) is not bool or not isinstance(row['aliases'], list)
                or len(row['aliases']) > 20):
            raise ValueError('Для каждой валюты: переключатель и не более 20 названий')
        aliases = []
        for alias in row['aliases']:
            if not isinstance(alias, str) or not re.fullmatch(r'[^\W\d_]+(?:[ -][^\W\d_]+)*', alias.strip(), re.UNICODE) or len(alias.strip()) > 40:
                raise ValueError('Название валюты: до 40 букв, пробелы и дефисы')
            alias = alias.strip().casefold()
            builtin = amounts_in('1 ' + alias)
            if (builtin and builtin[0]['unit'] != code) or (alias in owners and owners[alias] != code):
                raise ValueError('Название «' + alias + '» уже относится к другой валюте')
            owners[alias] = code
            if alias not in aliases:
                aliases.append(alias)
        result[code] = {'enabled': row['enabled'], 'aliases': aliases}
    return result
