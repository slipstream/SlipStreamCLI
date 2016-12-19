from __future__ import absolute_import, unicode_literals

import configparser
import os
import sys
import traceback
import collections

import six
from requests.exceptions import HTTPError

import click
from pprint import pformat
from prettytable import PrettyTable

from . import __version__, types, conf
from .base import AliasedGroup, Config, pass_config
from .log import logger
from slipstream.api import Api

try:
    from defusedxml import cElementTree as etree
except ImportError:
    from defusedxml import ElementTree as etree


def _excepthook(exctype, value, tb):
    if exctype == HTTPError:
        if 'xml' in value.response.headers['content-type']:
            root = etree.fromstring(value.response.text)
            logger.fatal(root.text)
        elif value.response.status_code == 401:
            logger.fatal("Authentication cookie expired. "
                        "Log in with `slipstream login`.")
        elif value.response.status_code == 403:
            logger.fatal("Invalid credentials provided. "
                        "Log in with `slipstream login`.")
        else:
            logger.fatal(str(value))
    else:
        logger.fatal(str(value))

    out = six.StringIO()
    traceback.print_exception(exctype, value, tb, file=out)
    logger.debug(out.getvalue())

sys.excepthook = _excepthook


def to_recursive_dict_or_string(d):
    if len(d) == 1 and d.keys()[0] is None:
        return d.values()[0]

    r = {}
    for k,v in six.iteritems(d):
        if isinstance(k, tuple) and len(k) == 2:
            a, b = k
            r.setdefault(a, {})[b] = v
        else:
            r[k] = v
    return r


def pp(element, level=0):
    m = ''
    if isinstance(element, dict):
        for k, v in element.iteritems():
            m += '\n{}- {} => {}'.format(' '*2*level, k, pp(v, level+1))
    elif hasattr(element, '__iter__'):
        for el in element:
            m += '\n{}- {}'.format(' '*2*level, pp(el, level+1))
    else:
        m = '{}'.format(element)
    return m


def printtable(items):
    table = PrettyTable(items[0]._fields)
    table.align = 'l'
    for item in items:
        table.add_row(item)
    click.echo(table)


def use_profile(ctx, param, value):
    cfg = ctx.ensure_object(Config)
    if value is not None:
        cfg.profile = value
    return value


def read_config(ctx, param, value):
    cfg = ctx.ensure_object(Config)
    if value is not None:
        cfg.filename = value
    try:
        cfg.read_config()
    except configparser.NoSectionError:
        raise click.BadParameter("Profile '%s' does not exists." % cfg.profile,
                                param=cli.params[0])
    return value


def config_set(ctx, param, value):
    cfg = ctx.ensure_object(Config)
    if value is not None:
        cfg.settings[param.name] = value
    return value

click.disable_unicode_literals_warning = True

@click.command(cls=AliasedGroup, context_settings=dict(help_option_names=['-h', '--help']))
@click.option('-P', '--profile', metavar='PROFILE',
              callback=use_profile, expose_value=False, is_eager=True,
              help="The config file section to use instead of '%s'."
                   % conf.DEFAULT_PROFILE)
@click.option('-c', '--config', type=click.Path(exists=True, dir_okay=False),
              callback=read_config, expose_value=False,
              help="The config file to use instead of '%s'."
                   % conf.DEFAULT_CONFIG_FILE)
@click.option('-u', '--username', metavar='USERNAME',
              callback=config_set, expose_value=False,
              help="The SlipStream username to connect with.")
@click.option('-p', '--password', metavar='PASSWORD',
              help="The SlipStream password to connect with.")
@click.option('-e', '--endpoint', type=types.URL(), metavar='URL',
              callback=config_set, expose_value=False,
              help='The SlipStream endpoint to use.')
@click.option('-i', '--insecure', is_flag=True, flag_value=True,
              callback=config_set, expose_value=False, default=False,
              help="Do not fail if SSL security checks fail.")
@click.option('-b', '--batch_mode', is_flag=True, flag_value=True,
              expose_value=True, default=False,
              help="Never enter interactive mode.")
@click.option('-q', '--quiet', 'quiet', count=True,
              help="Give less output. Can be used up to 3 times.")
@click.option('-v', '--verbose', 'verbose', count=True,
              help="Give more output. Can be used up to 4 times.")
@click.version_option(__version__, '-V', '--version')
@click.pass_context
def cli(ctx, password, batch_mode, quiet, verbose):
    """
    SlipStream command line tool.
    """
    # Configure logging
    level = 3  # Notify
    level -= verbose
    level += quiet
    logger.set_level(level)
    if level < 0:
        logger.enable_http_logging()

    # Attach Config object to context for subsequent use
    cfg = ctx.obj

    cfg.batch_mode = batch_mode

    args = (ctx.args + ctx.protected_args + [ctx.invoked_subcommand])

    if 'aliases' in args:
        return

    # Ask for credentials to the user when (s)he hasn't provided some
    if password or (not os.path.isfile(cfg.settings['cookie_file'])
                    and 'logout' not in args
                    and 'login' not in args):
        ctx.invoke(login, password=password)

    # Attach Api object to context for subsequent use
    ctx.obj = Api(cfg.settings['endpoint'], 
                  cfg.settings['cookie_file'], 
                  cfg.settings['insecure'])


@cli.command()
@pass_config
def aliases(cfg):
    """
    List currently defined aliases.
    """
    Alias = collections.namedtuple('Alias', ['command', 'aliases'])

    aliases = collections.defaultdict(lambda: [])
    for alias_cmd, real_cmd in six.iteritems(cfg.aliases):
        aliases[real_cmd].append(alias_cmd)
    
    aliases_table = [Alias(cmd, ', '.join(cmd_als))
                     for cmd, cmd_als in six.iteritems(aliases)]

    printtable(sorted(aliases_table, key=lambda x: x.command))


@cli.command()
@click.option('-u', '--username', metavar='USERNAME',
              callback=config_set, expose_value=False,
              help="The SlipStream username to connect with")
@click.option('-p', '--password', metavar='PASSWORD',
              help="The SlipStream password to connect with")
@click.option('-e', '--endpoint', type=types.URL(), metavar='URL',
              callback=config_set, expose_value=False,
              help='The SlipStream endpoint to use')
@pass_config
def login(cfg, password):
    """
    Log in with your slipstream credentials.
    """
    should_prompt = True if not cfg.batch_mode else False
    api = Api(cfg.settings['endpoint'],
              cfg.settings['cookie_file'], 
              cfg.settings['insecure'])
    username = cfg.settings.get('username')

    if (username and password) or cfg.batch_mode:
        try:
            api.login(username, password)
        except HTTPError as e:
            if e.response.status_code != 401:
                raise
            logger.warning("Invalid credentials provided.")
            if cfg.batch_mode:
                sys.exit(3)
        else:
            should_prompt = False

    while should_prompt:
        logger.notify("Enter your SlipStream credentials.")
        if username is None:
            username = click.prompt("Username")
        password = click.prompt("Password for '{}'".format(username),
                                hide_input=True)

        try:
            api.login(username, password)
        except HTTPError as e:
            if e.response.status_code != 401:
                raise
            logger.error("Authentication failed.")
        else:
            cfg.settings['username'] = username
            logger.notify("Authentication successful.")
            should_prompt = False

    cfg.write_config()
    logger.info("Local credentials saved.")


@cli.command()
@click.pass_obj
def logout(api):
    """
    Clear local authentication credentials.
    """
    api.logout()
    logger.notify("Local credentials cleared.")


@cli.command()
@click.pass_obj
def appstore(api):
    """List available applications in the app store."""
    apps = [app for app in api.list_applications()]
    if apps:
        printtable(apps)
    else:
        logger.warning("No applications found in the appstore.")


@cli.command('list')
@click.pass_obj
@click.option('-k', '--type',
              type=click.Choice(['application', 'component', 'project']),
              help="List only elements of the specified type.")
@click.option('-r', '--recurse', 'recurse', is_flag=True, default=False,
              help="List projects recursively.")
@click.argument('path', required=False)
def list_project_content(api, type, recurse, path):
    """
    List project content.

    If path is not given, starts from the root project.
    """
    def filter_func(module):
        if type is not None and module.type != type:
            return False
        return True

    try:
        modules = [module for module in api.list_project_content(path, recurse)
                   if filter_func(module)]
    except HTTPError as e:
        if e.response.status_code == 404:
            raise click.ClickException("Module '{0}' doesn't exists.".format(path))
        raise
    if modules:
        printtable(modules)
    else:
        logger.warning("No element found matching your criteria.")


@cli.command()
@click.argument('deployment_id', type=click.UUID, required=True)
@click.pass_obj
def deployment(api, deployment_id):
    """
    Show a deployment
    """
    deployment = api.get_deployment(deployment_id)
    if deployment:
        printtable([deployment])
    else:
        logger.warning("Deployment not found.")


@cli.command()
@click.option('-i', '--inactive', 'inactive', is_flag=True, default=False,
              help="Include inactive runs.")
@click.pass_obj
def deployments(api, inactive):
    """
    List deployments
    """
    deployments = [deployment for deployment in api.list_deployments(inactive)]
    if deployments:
        printtable(deployments)
    else:
        logger.warning("No deployment found.")


@cli.command()
@click.option('--deployment-id', 'deployment_id', metavar='UUID', type=click.UUID,
              help="The deployment UUID to filter with.")
@click.option('--cloud', metavar='CLOUD', type=click.STRING,
              help="The cloud service name to filter with.")
@click.option('--status', metavar='STATUS', type=click.STRING,
              help="The status to filter with.")
@click.pass_obj
def virtualmachines(api, deployment_id, cloud, status):
    """
    List virtual machines filtered according to given options.
    """
    def filter_func(vm):
        if deployment_id and vm.deployment_id != deployment_id:
            return False
        if cloud and vm.cloud != cloud:
            return False
        if status and vm.status != status:
            return False
        return True

    vms = [vm for vm in api.list_virtualmachines() if filter_func(vm)]
    if vms:
        printtable(vms)
    else:
        logger.warning("No virtual machines found matching your criteria.")


@cli.command()
@click.option('--cloud', help="The cloud service to run the image with.")
@click.option('--open', 'should_open', is_flag=True, default=False,
              help="Open the created run in a web browser.")
@click.argument('path', metavar='PATH', required=True)
@click.pass_context
def build(ctx, cloud, should_open, path):
    """
    Build the given component
    """
    api = ctx.obj
    deployment_id = api.build_component(path, cloud)
    click.echo(deployment_id)
    if should_open:
        ctx.invoke(open_cmd, run_id=deployment_id)


@cli.command()
@click.option('--cloud', '-c', type=types.NodeKeyValue(), multiple=True, metavar='<node>:<cloud> or <cloud>',
              help='Specify cloud service to be used. If not specified, it will try to find the cheapest Cloud; fallback to your default Cloud.')
@click.option('--param', '-p', type=types.NodeKeyValue(), multiple=True,
              metavar='<node>:<param_name>=<value> or <param_name>=<value>',
              help='Set application or component parameters.')
@click.option('--open', 'should_open', is_flag=True, default=False,
              help="Open the created run in a web browser")
@click.option('--dry-run', '-d', 'dry_run', is_flag=True, default=False,
              help="Doesn't launch the deployment")
@click.argument('path', metavar='PATH', nargs=1, required=True)
@click.pass_context
def deploy(ctx, cloud, param, should_open, dry_run, path):
    """
    Deploy a component or an application
    """
    api = ctx.obj
    type = 'Unknown'

    try:
        type_ = api.get_element(path).type
    except HTTPError as e:
        if e.response.status_code == 404:
            app = {app.name: app for app in api.list_applications()}.get(path)
            if app is None:
                raise
            path = app.path
            type_ = app.type

    #if type_ not in ['application', 'component']:
    #    raise click.ClickException("Cannot deploy a '{}'.".format(type))

    params = to_recursive_dict_or_string(dict(param))
    cloud_params = to_recursive_dict_or_string(dict(cloud))

    prices = {}
    clouds = {}
    try:
        logger.info('Searching the cheapest service offer')
        offers = api.find_service_offers(path)
        if type_ == 'component' and not cloud_params:
            best_offer = offers.values()[0][0]
            clouds = best_offer['name']
            prices = best_offer['price']
        elif type_ == 'application':
            best_offers = {nodename: node_offers[0] for nodename, node_offers in six.iteritems(offers)}
            clouds = {nodename: offer['name'] for nodename, offer in six.iteritems(best_offers)
                      if nodename not in cloud_params}
            prices = {nodename: offer['price'] for nodename, offer in six.iteritems(best_offers)}
    except:
        raise #pass
    
    if not clouds:
        logger.warning('Failed to find the cheapest Cloud. Will use your default Cloud.')

    if type_ == 'component' and not clouds:
        clouds = cloud_params
    elif type_ == 'application':
        clouds.update(cloud_params)

    if dry_run:
        message = "Not sending the request to deploy: {}\n".format(path) + \
                  "- with the following parameters: {}\n".format(pp(params, 1)) + \
                  "- on the following cloud(s): {}\n".format(pp(clouds, 1)) + \
                  "- with the following price(s): {}\n".format(pp(prices, 1)) + \
                  ""
        click.echo(message)
        return

    deployment_id = api.deploy(path, cloud=clouds, parameters=params)
    click.echo(deployment_id)
    if should_open:
        ctx.invoke(open_cmd, run_id=deployment_id)


@cli.command()
@click.argument('path', metavar='PATH', nargs=1, required=True)
@click.pass_obj
def show(api, path):
    """
    Show project, component or application details
    """
    element = api.get_element(path)

    if element:
        printtable([element])
    else:
        logger.warning("Element not found.")


@cli.command('open')
@click.argument('deployment_id', metavar='UUID', type=click.UUID)
@click.pass_obj
def open_cmd(api, deployment_id):
    """
    Open the given deployment in a web browser.
    """
    click.launch("{0}/run/{1}".format(api.endpoint, deployment_id))


@cli.command()
@click.argument('deployment_id', metavar='UUID', type=click.UUID)
@click.pass_obj
def terminate(api, deployment_id):
    """
    Terminate the given deployment.
    """
    api.terminate(deployment_id)
    logger.info("Deployment successfully terminated.")


@cli.command()
@click.pass_obj
def usage(api):
    """
    List current usage and quota by cloud service.
    """
    items = [item for item in api.usage()]
    printtable(items)


@cli.command()
@click.pass_obj
@click.argument('path', metavar='PATH', nargs=1, required=True)
@click.argument('version', metavar='VERSION', type=int, required=False)
def publish(api, path, version):
    """
    Publish PATH and VERSION to the AppStore.

    If VERSION is not given, assumes the latest one.

    WARNING: you need to be a superuser to publish module.
    """
    if version is None:
        version = api.get_element(path).version
    try:
        api.publish('%s/%s' % (path, version))
    except HTTPError as e:
        if e.response.status_code == 403:
            raise click.ClickException("Only superuser is allowed to publish.")
        elif e.response.status_code == 404:
            raise click.ClickException(
                "'%s' #%d doesn't exists." % (path, version))
        elif e.response.status_code == 409:
            logger.warning(
                "'%s' #%d is already published." % (path, version))
        else:
            raise
    else:
        logger.notify("'%s' #%d published." % (path, version))


@cli.command()
@click.pass_obj
@click.argument('path', metavar='PATH', nargs=1, required=True)
@click.argument('version', metavar='VERSION', type=int, required=False)
def unpublish(api, path, version):
    """
    Unpublish PATH and VERSION to the AppStore.

    If VERSION is not given, assumes the latest one.

    WARNING: you need to be a superuser to publish module.
    """
    if version is None:
        version = api.get_element(path).version
    try:
        api.unpublish('%s/%s' % (path, version))
    except HTTPError as e:
        if e.response.status_code == 403:
            raise click.ClickException("Only a superuser is allowed to unpublish.")
        elif e.response.status_code == 404:
            raise click.ClickException(
                "'%s' #%d doesn't exists." % (path, version))
        else:
            raise
    else:
        logger.notify("'%s' #%d unpublished." % (path, version))


@cli.command()
@click.pass_obj
@click.argument('path', metavar='PATH', nargs=1, required=True)
@click.argument('version', metavar='VERSION', type=int, required=False)
def delete(api, path, version):
    """
    Delete an element (project/component/application).
    """
    logger.debug(path)
    if version is not None:
        path = '%s/%s' % (path, version)

    try:
        api.delete_element(path)
    except HTTPError as e:
        if e.response.status_code == 404:
            raise click.ClickException("%s don't exist." % path)
        raise

    logger.notify('Deleted %s' % path)


