from __future__ import annotations as _annotations

import re
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, IPv6Address, IPv6Interface, IPv6Network
from typing import TYPE_CHECKING, Any, Callable, Collection, Generator, Match, Pattern, cast, no_type_check

import typing_extensions
from pydantic_core import PydanticCustomError, core_schema

from ._internal import _repr, _utils

if TYPE_CHECKING:
    import email_validator
    from typing_extensions import TypedDict

    CallableGenerator = Generator[Callable[..., Any], None, None]

    class Parts(TypedDict, total=False):
        scheme: str
        user: str | None
        password: str | None
        ipv4: str | None
        ipv6: str | None
        domain: str | None
        port: str | None
        path: str | None
        query: str | None
        fragment: str | None

    class HostParts(TypedDict, total=False):
        host: str
        tld: str | None
        host_type: str | None
        port: str | None
        rebuild: bool

    NetworkType = str | bytes | int | tuple[str | bytes | int, str | int]  # type: ignore[misc]

else:
    email_validator = None

    class Parts(dict):
        pass


__all__ = [
    'AnyUrl',
    'AnyHttpUrl',
    'FileUrl',
    'HttpUrl',
    'stricturl',
    'EmailStr',
    'NameEmail',
    'IPvAnyAddress',
    'IPvAnyInterface',
    'IPvAnyNetwork',
    'PostgresDsn',
    'CockroachDsn',
    'AmqpDsn',
    'RedisDsn',
    'MongoDsn',
    'KafkaDsn',
    'validate_email',
]

_url_regex_cache = None
_multi_host_url_regex_cache = None
_ascii_domain_regex_cache = None
_int_domain_regex_cache = None
_host_regex_cache = None

_host_regex = (
    r'(?:'
    r'(?P<ipv4>(?:\d{1,3}\.){3}\d{1,3})(?=$|[/:#?])|'  # ipv4
    r'(?P<ipv6>\[[A-F0-9]*:[A-F0-9:]+\])(?=$|[/:#?])|'  # ipv6
    r'(?P<domain>[^\s/:?#]+)'  # domain, validation occurs later
    r')?'
    r'(?::(?P<port>\d+))?'  # port
)
_scheme_regex = r'(?:(?P<scheme>[a-z][a-z0-9+\-.]+)://)?'  # scheme https://tools.ietf.org/html/rfc3986#appendix-A
_user_info_regex = r'(?:(?P<user>[^\s:/]*)(?::(?P<password>[^\s/]*))?@)?'
_path_regex = r'(?P<path>/[^\s?#]*)?'
_query_regex = r'(?:\?(?P<query>[^\s#]*))?'
_fragment_regex = r'(?:#(?P<fragment>[^\s#]*))?'


def url_regex() -> Pattern[str]:
    global _url_regex_cache
    if _url_regex_cache is None:
        _url_regex_cache = re.compile(
            rf'{_scheme_regex}{_user_info_regex}{_host_regex}{_path_regex}{_query_regex}{_fragment_regex}',
            re.IGNORECASE,
        )
    return _url_regex_cache


def multi_host_url_regex() -> Pattern[str]:
    """
    Compiled multi host url regex.

    Additionally to `url_regex` it allows to match multiple hosts.
    E.g. host1.db.net,host2.db.net
    """
    global _multi_host_url_regex_cache
    if _multi_host_url_regex_cache is None:
        _multi_host_url_regex_cache = re.compile(
            rf'{_scheme_regex}{_user_info_regex}'
            r'(?P<hosts>([^/]*))'  # validation occurs later
            rf'{_path_regex}{_query_regex}{_fragment_regex}',
            re.IGNORECASE,
        )
    return _multi_host_url_regex_cache


def ascii_domain_regex() -> Pattern[str]:
    global _ascii_domain_regex_cache
    if _ascii_domain_regex_cache is None:
        ascii_chunk = r'[_0-9a-z](?:[-_0-9a-z]{0,61}[_0-9a-z])?'
        ascii_domain_ending = r'(?P<tld>\.[a-z]{2,63})?\.?'
        _ascii_domain_regex_cache = re.compile(
            fr'(?:{ascii_chunk}\.)*?{ascii_chunk}{ascii_domain_ending}', re.IGNORECASE
        )
    return _ascii_domain_regex_cache


def int_domain_regex() -> Pattern[str]:
    global _int_domain_regex_cache
    if _int_domain_regex_cache is None:
        int_chunk = r'[_0-9a-\U00040000](?:[-_0-9a-\U00040000]{0,61}[_0-9a-\U00040000])?'
        int_domain_ending = r'(?P<tld>(\.[^\W\d_]{2,63})|(\.(?:xn--)[_0-9a-z-]{2,63}))?\.?'
        _int_domain_regex_cache = re.compile(fr'(?:{int_chunk}\.)*?{int_chunk}{int_domain_ending}', re.IGNORECASE)
    return _int_domain_regex_cache


def host_regex() -> Pattern[str]:
    global _host_regex_cache
    if _host_regex_cache is None:
        _host_regex_cache = re.compile(
            _host_regex,
            re.IGNORECASE,
        )
    return _host_regex_cache


class AnyUrl(str):
    strip_whitespace = True
    min_length = 1
    max_length = 2**16
    allowed_schemes: Collection[str] | None = None
    tld_required: bool = False
    user_required: bool = False
    host_required: bool = True
    hidden_parts: set[str] = set()

    __slots__ = ('scheme', 'user', 'password', 'host', 'tld', 'host_type', 'port', 'path', 'query', 'fragment')

    @no_type_check
    def __new__(cls, url: str | None, **kwargs) -> object:
        return str.__new__(cls, cls.build(**kwargs) if url is None else url)

    def __init__(
        self,
        url: str,
        *,
        scheme: str,
        user: str | None = None,
        password: str | None = None,
        host: str | None = None,
        tld: str | None = None,
        host_type: str = 'domain',
        port: str | None = None,
        path: str | None = None,
        query: str | None = None,
        fragment: str | None = None,
    ) -> None:
        str.__init__(url)
        self.scheme = scheme
        self.user = user
        self.password = password
        self.host = host
        self.tld = tld
        self.host_type = host_type
        self.port = port
        self.path = path
        self.query = query
        self.fragment = fragment

    @classmethod
    def build(
        cls,
        *,
        scheme: str,
        user: str | None = None,
        password: str | None = None,
        host: str,
        port: str | None = None,
        path: str | None = None,
        query: str | None = None,
        fragment: str | None = None,
        **_kwargs: str,
    ) -> str:
        parts = Parts(  # type: ignore[misc]
            scheme=scheme,
            user=user,
            password=password,
            host=host,
            port=port,
            path=path,
            query=query,
            fragment=fragment,
            **_kwargs,
        )

        url = scheme + '://'
        if user:
            url += user
        if password:
            url += ':' + password
        if user or password:
            url += '@'
        url += host
        if port and ('port' not in cls.hidden_parts or cls.get_default_parts(parts).get('port') != port):
            url += ':' + port
        if path:
            url += path
        if query:
            url += '?' + query
        if fragment:
            url += '#' + fragment
        return url

    @classmethod
    def __modify_schema__(cls, field_schema: dict[str, Any]) -> None:
        _utils.update_not_none(field_schema, minLength=cls.min_length, maxLength=cls.max_length, format='uri')

    @classmethod
    def __get_pydantic_validation_schema__(cls, **_kwargs: Any) -> core_schema.FunctionSchema:
        return core_schema.FunctionSchema(
            type='function',
            mode='after',
            function=cls.validate,
            schema=core_schema.StringSchema(
                type='str',
                min_length=cls.min_length,
                max_length=cls.max_length,
                strip_whitespace=cls.strip_whitespace,
            ),
        )

    @classmethod
    def validate(cls, __input_value: str, **_kwargs: Any) -> str | AnyUrl:
        if __input_value.__class__ == cls:
            return __input_value
        url = __input_value
        m = cls._match_url(url)
        # the regex should always match, if it doesn't please report with details of the URL tried
        assert m, 'URL regex failed unexpectedly'

        original_parts = cast('Parts', m.groupdict())
        parts = cls.apply_default_parts(original_parts)
        parts = cls.validate_parts(parts)

        if m.end() != len(url):
            raise PydanticCustomError(
                'url.extra',
                'URL invalid, extra characters found after valid URL: {extra}',
                {'extra': repr(url[m.end() :])},
            )

        return cls._build_url(m, url, parts)

    @classmethod
    def _build_url(cls, m: Match[str], url: str, parts: 'Parts') -> 'AnyUrl':
        """
        Validate hosts and build the AnyUrl object. Split from `validate` so this method
        can be altered in `MultiHostDsn`.
        """
        host, tld, host_type, rebuild = cls.validate_host(parts)

        return cls(
            None if rebuild else url,
            scheme=parts['scheme'],
            user=parts['user'],
            password=parts['password'],
            host=host,
            tld=tld,
            host_type=host_type,
            port=parts['port'],
            path=parts['path'],
            query=parts['query'],
            fragment=parts['fragment'],
        )

    @staticmethod
    def _match_url(url: str) -> Match[str] | None:
        return url_regex().match(url)

    @staticmethod
    def _validate_port(port: str | None) -> None:
        if port is not None and int(port) > 65_535:
            raise PydanticCustomError('url.port', 'URL port invalid, port cannot exceed 65535')

    @classmethod
    def validate_parts(cls, parts: 'Parts', validate_port: bool = True) -> 'Parts':
        """
        A method used to validate parts of a URL.
        Could be overridden to set default values for parts if missing
        """
        scheme = parts['scheme']
        if scheme is None:
            raise PydanticCustomError('url.scheme', 'invalid or missing URL scheme')

        if cls.allowed_schemes and scheme.lower() not in cls.allowed_schemes:
            raise PydanticCustomError(
                'url.scheme',
                'URL scheme not permitted',
                {'allowed_schemes': ', '.join(sorted(set(cls.allowed_schemes)))},
            )

        if validate_port:
            cls._validate_port(parts['port'])

        user = parts['user']
        if cls.user_required and user is None:
            raise PydanticCustomError('url.userinfo', 'userinfo required in URL but missing')
        return parts

    @classmethod
    def validate_host(cls, parts: 'Parts') -> tuple[str, str | None, str, bool]:
        tld, host_type, rebuild = None, None, False
        for f in ('domain', 'ipv4', 'ipv6'):
            host = parts[f]  # type: ignore[literal-required]
            if host:
                host_type = f
                break

        if host is None:
            if cls.host_required:
                raise PydanticCustomError('url.host', 'URL host invalid')
        elif host_type == 'domain':
            is_international = False
            d = ascii_domain_regex().fullmatch(host)
            if d is None:
                d = int_domain_regex().fullmatch(host)
                if d is None:
                    raise PydanticCustomError('url.host', 'URL host invalid')
                is_international = True

            tld = d.group('tld')
            if tld is None and not is_international:
                d = int_domain_regex().fullmatch(host)
                assert d is not None
                tld = d.group('tld')
                is_international = True

            if tld is not None:
                tld = tld[1:]
            elif cls.tld_required:
                raise PydanticCustomError('url.host', 'URL host invalid, top level domain required')

            if is_international:
                host_type = 'int_domain'
                rebuild = True
                host = host.encode('idna').decode('ascii')
                if tld is not None:
                    tld = tld.encode('idna').decode('ascii')

        return host, tld, host_type, rebuild  # type: ignore

    @staticmethod
    def get_default_parts(parts: 'Parts') -> 'Parts':
        return {}

    @classmethod
    def apply_default_parts(cls, parts: 'Parts') -> 'Parts':
        for key, value in cls.get_default_parts(parts).items():
            if not parts[key]:  # type: ignore[literal-required]
                parts[key] = value  # type: ignore[literal-required]
        return parts

    def __repr__(self) -> str:
        extra = ', '.join(f'{n}={getattr(self, n)!r}' for n in self.__slots__ if getattr(self, n) is not None)
        return f'{self.__class__.__name__}({super().__repr__()}, {extra})'


class AnyHttpUrl(AnyUrl):
    allowed_schemes = {'http', 'https'}

    __slots__ = ()


class HttpUrl(AnyHttpUrl):
    tld_required = True
    # https://stackoverflow.com/questions/417142/what-is-the-maximum-length-of-a-url-in-different-browsers
    max_length = 2083
    hidden_parts = {'port'}

    @staticmethod
    def get_default_parts(parts: 'Parts') -> 'Parts':
        return {'port': '80' if parts['scheme'] == 'http' else '443'}


class FileUrl(AnyUrl):
    allowed_schemes = {'file'}
    host_required = False

    __slots__ = ()


class MultiHostDsn(AnyUrl):
    __slots__ = AnyUrl.__slots__ + ('hosts',)

    def __init__(self, *args: Any, hosts: list[HostParts] | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.hosts = hosts

    @staticmethod
    def _match_url(url: str) -> Match[str] | None:
        return multi_host_url_regex().match(url)

    @classmethod
    def validate_parts(cls, parts: 'Parts', validate_port: bool = True) -> 'Parts':
        return super().validate_parts(parts, validate_port=False)

    @classmethod
    def _build_url(cls, m: Match[str], url: str, parts: 'Parts') -> 'MultiHostDsn':
        hosts_parts: list[HostParts] = []
        host_re = host_regex()
        for host in m.groupdict()['hosts'].split(','):
            d: Parts = host_re.match(host).groupdict()  # type: ignore
            host, tld, host_type, rebuild = cls.validate_host(d)
            port = d.get('port')
            cls._validate_port(port)
            hosts_parts.append(
                {
                    'host': host,
                    'host_type': host_type,
                    'tld': tld,
                    'rebuild': rebuild,
                    'port': port,
                }
            )

        if len(hosts_parts) > 1:
            return cls(
                None if any([hp['rebuild'] for hp in hosts_parts]) else url,
                scheme=parts['scheme'],
                user=parts['user'],
                password=parts['password'],
                path=parts['path'],
                query=parts['query'],
                fragment=parts['fragment'],
                host_type=None,
                hosts=hosts_parts,
            )
        else:
            # backwards compatibility with single host
            host_part = hosts_parts[0]
            return cls(
                None if host_part['rebuild'] else url,
                scheme=parts['scheme'],
                user=parts['user'],
                password=parts['password'],
                host=host_part['host'],
                tld=host_part['tld'],
                host_type=host_part['host_type'],
                port=host_part.get('port'),
                path=parts['path'],
                query=parts['query'],
                fragment=parts['fragment'],
            )


class PostgresDsn(MultiHostDsn):
    allowed_schemes = {
        'postgres',
        'postgresql',
        'postgresql+asyncpg',
        'postgresql+pg8000',
        'postgresql+psycopg',
        'postgresql+psycopg2',
        'postgresql+psycopg2cffi',
        'postgresql+py-postgresql',
        'postgresql+pygresql',
    }
    user_required = True

    __slots__ = ()


class CockroachDsn(AnyUrl):
    allowed_schemes = {
        'cockroachdb',
        'cockroachdb+psycopg2',
        'cockroachdb+asyncpg',
    }
    user_required = True


class AmqpDsn(AnyUrl):
    allowed_schemes = {'amqp', 'amqps'}
    host_required = False


class RedisDsn(AnyUrl):
    __slots__ = ()
    allowed_schemes = {'redis', 'rediss'}
    host_required = False

    @staticmethod
    def get_default_parts(parts: 'Parts') -> 'Parts':
        return {
            'domain': 'localhost' if not (parts['ipv4'] or parts['ipv6']) else '',
            'port': '6379',
            'path': '/0',
        }


class MongoDsn(AnyUrl):
    allowed_schemes = {'mongodb'}

    # TODO: Needed to generic "Parts" for "Replica Set", "Sharded Cluster", and other mongodb deployment modes
    @staticmethod
    def get_default_parts(parts: 'Parts') -> 'Parts':
        return {'port': '27017'}


class KafkaDsn(AnyUrl):
    allowed_schemes = {'kafka'}

    @staticmethod
    def get_default_parts(parts: 'Parts') -> 'Parts':
        return {'domain': 'localhost', 'port': '9092'}


def stricturl(
    *,
    strip_whitespace: bool = True,
    min_length: int = 1,
    max_length: int = 2**16,
    tld_required: bool = True,
    host_required: bool = True,
    allowed_schemes: Collection[str] | None = None,
) -> type[AnyUrl]:
    # use kwargs then define conf in a dict to aid with IDE type hinting
    namespace = dict(
        strip_whitespace=strip_whitespace,
        min_length=min_length,
        max_length=max_length,
        tld_required=tld_required,
        host_required=host_required,
        allowed_schemes=allowed_schemes,
    )
    return type('UrlValue', (AnyUrl,), namespace)


def import_email_validator() -> None:
    global email_validator
    try:
        import email_validator
    except ImportError as e:
        raise ImportError('email-validator is not installed, run `pip install pydantic[email]`') from e


if TYPE_CHECKING:
    EmailStr = typing_extensions.Annotated[str, ...]
else:

    class EmailStr:
        @classmethod
        def __get_pydantic_validation_schema__(
            cls, schema: core_schema.CoreSchema | None = None, **_kwargs: Any
        ) -> core_schema.CoreSchema:
            import_email_validator()
            if schema is None:
                return core_schema.function_after_schema(core_schema.string_schema(), cls.validate)
            else:
                assert schema['type'] == 'str', 'EmailStr must be used with string fields'
                return core_schema.function_after_schema(schema, cls.validate)

        @classmethod
        def __modify_schema__(cls, field_schema: dict[str, Any]) -> None:
            field_schema.update(type='string', format='email')

        @classmethod
        def validate(cls, __input_value: str, **_kwargs: Any) -> str:
            return validate_email(__input_value)[1]


class NameEmail(_repr.Representation):
    __slots__ = 'name', 'email'

    def __init__(self, name: str, email: str):
        self.name = name
        self.email = email

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, NameEmail) and (self.name, self.email) == (other.name, other.email)

    @classmethod
    def __modify_schema__(cls, field_schema: dict[str, Any]) -> None:
        field_schema.update(type='string', format='name-email')

    @classmethod
    def __get_pydantic_validation_schema__(cls, **_kwargs: Any) -> core_schema.FunctionSchema:
        import_email_validator()
        return core_schema.function_after_schema(
            core_schema.union_schema(
                core_schema.is_instance_schema(cls),
                core_schema.string_schema(),
            ),
            cls.validate,
        )

    @classmethod
    def validate(cls, __input_value: NameEmail | str, **_kwargs: Any) -> NameEmail:
        if isinstance(__input_value, cls):
            return __input_value
        else:
            name, email = validate_email(__input_value)  # type: ignore[arg-type]
            return cls(name, email)

    def __str__(self) -> str:
        return f'{self.name} <{self.email}>'


class IPvAnyAddress:
    __slots__ = ()

    def __new__(cls, value: Any) -> IPv4Address | IPv6Address:  # type: ignore[misc]
        try:
            return IPv4Address(value)
        except ValueError:
            pass

        try:
            return IPv6Address(value)
        except ValueError:
            raise PydanticCustomError('ip_any_address', 'value is not a valid IPv4 or IPv6 address')

    @classmethod
    def __modify_schema__(cls, field_schema: dict[str, Any]) -> None:
        field_schema.update(type='string', format='ipvanyaddress')

    @classmethod
    def __get_pydantic_validation_schema__(cls, **_kwargs: Any) -> core_schema.FunctionPlainSchema:
        return core_schema.function_plain_schema(cls._validate)

    @classmethod
    def _validate(cls, __input_value: Any, **_kwargs: Any) -> IPv4Address | IPv6Address:
        return cls(__input_value)  # type: ignore[return-value]


class IPvAnyInterface:
    __slots__ = ()

    def __new__(cls, value: NetworkType) -> IPv4Interface | IPv6Interface:  # type: ignore[misc]
        try:
            return IPv4Interface(value)
        except ValueError:
            pass

        try:
            return IPv6Interface(value)
        except ValueError:
            raise PydanticCustomError('ip_any_interface', 'value is not a valid IPv4 or IPv6 interface')

    @classmethod
    def __modify_schema__(cls, field_schema: dict[str, Any]) -> None:
        field_schema.update(type='string', format='ipvanyinterface')

    @classmethod
    def __get_pydantic_validation_schema__(cls, **_kwargs: Any) -> core_schema.FunctionPlainSchema:
        return core_schema.function_plain_schema(cls._validate)

    @classmethod
    def _validate(cls, __input_value: NetworkType, **_kwargs: Any) -> IPv4Interface | IPv6Interface:
        return cls(__input_value)  # type: ignore[return-value]


class IPvAnyNetwork:
    __slots__ = ()

    def __new__(cls, value: NetworkType) -> IPv4Network | IPv6Network:  # type: ignore[misc]
        # Assume IP Network is defined with a default value for ``strict`` argument.
        # Define your own class if you want to specify network address check strictness.
        try:
            return IPv4Network(value)
        except ValueError:
            pass

        try:
            return IPv6Network(value)
        except ValueError:
            raise PydanticCustomError('ip_any_network', 'value is not a valid IPv4 or IPv6 network')

    @classmethod
    def __modify_schema__(cls, field_schema: dict[str, Any]) -> None:
        field_schema.update(type='string', format='ipvanynetwork')

    @classmethod
    def __get_pydantic_validation_schema__(cls, **_kwargs: Any) -> core_schema.FunctionPlainSchema:
        return core_schema.function_plain_schema(cls._validate)

    @classmethod
    def _validate(cls, __input_value: NetworkType, **_kwargs: Any) -> IPv4Network | IPv6Network:
        return cls(__input_value)  # type: ignore[return-value]


pretty_email_regex = re.compile(r' *([\w ]*?) *<(.+?)> *')


def validate_email(value: str) -> tuple[str, str]:
    """
    Email address validation using https://pypi.org/project/email-validator/

    Notes:
    * raw ip address (literal) domain parts are not allowed.
    * "John Doe <local_part@domain.com>" style "pretty" email addresses are processed
    * spaces are striped from the beginning and end of addresses but no error is raised
    """
    if email_validator is None:
        import_email_validator()

    m = pretty_email_regex.fullmatch(value)
    name: str | None = None
    if m:
        name, value = m.groups()

    email = value.strip()

    try:
        parts = email_validator.validate_email(email, check_deliverability=False)
    except email_validator.EmailNotValidError as e:
        raise PydanticCustomError(
            'value_error', 'value is not a valid email address: {reason}', {'reason': str(e.args[0])}
        ) from e

    return name or parts['local'], parts['email']
