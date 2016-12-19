import re
import click

from six.moves.urllib.parse import urlparse


class URL(click.ParamType):
    name = 'url'

    def convert(self, value, param, ctx):
        if not isinstance(value, tuple):
            url = urlparse(value)
            if url.scheme not in ('http', 'https'):
                self.fail('invalid URL scheme (%s).  Only HTTP(S) URLs are '
                          'allowed' % url.scheme, param, ctx)
        return value


class NodeKeyValue(click.ParamType):
    name = 'nodekeyvalue'

    def convert(self, value, param, ctx):
        try:
            pattern = '^(?:([^:=]+):)?(?:([^:=]+)=)?([^:=]+)$'
            node, key, val = re.search(pattern, value).groups()
            
            if param.name != 'cloud' and key == '':
                raise ValueError

            if node is None: # Set a Component parameter/Cloud
                if param.name == 'param': # Set a Component parameter
                    return key, val
                elif param.name == 'cloud': # Set the Component Cloud
                    return None, val
            else: # Set a Node parameter/Cloud
                if param.name == 'param': # Set a Node parameter
                    return ((node, key), val)
                elif param.name == 'cloud': # Set the Node Cloud
                    return node, val

        except ValueError:
            raise
            self.fail("%s is not a valid!\nAuthorized format: %s" % (value, param.metavar), param, ctx)



