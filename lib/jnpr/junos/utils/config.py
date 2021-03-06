# utils/config.py
import os
import re

# 3rd-party modules
from lxml import etree
from ncclient.operations import RPCError

# package modules
from jnpr.junos.exception import *
from jnpr.junos import jxml as JXML
from jnpr.junos.utils.util import Util

"""
Configuration Utilities
"""


class Config(Util):

    """
    Overivew of Configuration Utilities:

    * :meth:`commit`: commit changes
    * :meth:`commit_check`: perform the commit check operation
    * :meth:`diff`: return the diff string between running and candidate config
    * :meth:`load`: load changes into the candidate config
    * :meth:`lock`: take an exclusive lock on the candidate config
    * :meth:`pdiff`: prints the diff string (debug/helper)
    * :meth:`rescue`: controls "rescue configuration"
    * :meth:`rollback`: perform the load rollback command
    * :meth:`unlock`: release the exclusive lock
    """

    # ------------------------------------------------------------------------
    # commit
    # ------------------------------------------------------------------------

    def commit(self, **kvargs):
        """
        Commit a configuration.

        :param str comment: If provided logs this comment with the commit.
        :param int confirm: If provided activates confirm safeguard with
                            provided value as timeout (minutes).
        :param int timeout: If provided the command will wait for completion
                            using the provided value as timeout (seconds).
                            By default the device timeout is used.
        :param bool sync: On dual control plane systems, requests that
                            the candidate configuration on one control plane be
                            copied to the other control plane, checked for
                            correct syntax, and committed on both Routing Engines.
        :param bool force_sync: On dual control plane systems, forces the candidate
                            configuration on one control plane to be copied to the
                            other control plane.
        :param bool full: When true requires all the daemons to check and evaluate
                            the new configuration.
        :param bool detail: When true return commit detail as XML


        :returns:
            * ``True`` when successful
            * Commit detail XML (when detail is True)

        :raises CommitError: When errors detected in candidate configuration.
                             You can use the Exception errs variable
                             to identify the specific problems

        .. warning::
            If the function does not receive a reply prior to the timeout
            a RpcTimeoutError will be raised.  It is possible the commit
            was successful.  Manual verification may be required.
        """
        rpc_args = {}

        # if a comment is provided, then include that in the RPC

        comment = kvargs.get('comment')
        if comment:
            rpc_args['log'] = comment

        # if confirm is provided, then setup the RPC args
        # so that Junos will either use the default confirm
        # timeout (confirm=True) or a specific timeout
        # (confirm=<minutes>)

        confirm = kvargs.get('confirm')
        if confirm:
            rpc_args['confirmed'] = True
            confirm_val = str(confirm)
            if 'True' != confirm_val:
                rpc_args['confirm-timeout'] = confirm_val

        # if a timeout is provided, then include that in the RPC

        timeout = kvargs.get('timeout')
        if timeout:
            rpc_args['dev_timeout'] = timeout

        # Check for force_sync and sync
        if kvargs.get('force_sync'):
            rpc_args['synchronize'] = True
            rpc_args['force-synchronize'] = True
        elif kvargs.get('sync'):
            rpc_args['synchronize'] = True

        # Check for full
        if kvargs.get('full'):
            rpc_args['full'] = True

        rpc_varg = []
        detail = kvargs.get('detail')
        if detail:
            rpc_varg = [{'detail': 'detail'}]

        # dbl-splat the rpc_args since we want to pass key/value to metaexec
        # if there is a commit/check error, this will raise an execption

        try:
            rsp = self.rpc.commit_configuration(*rpc_varg, **rpc_args)
        except RpcTimeoutError:
            raise
        except RpcError as err:        # jnpr.junos exception
            if err.rsp.find('ok') is not None:
                # this means there are warnings, but no errors
                return True
            else:
                raise CommitError(cmd=err.cmd, rsp=err.rsp)
        except Exception as err:
            # so the ncclient gives us something I don't want.  I'm going to
            # convert it and re-raise the commit error
            if hasattr(err, 'xml') and isinstance(err.xml, etree._Element):
                raise CommitError(rsp=err.xml)
            else:
                raise

        if detail:
            return rsp
        else:
            return True

    # -------------------------------------------------------------------------
    # commit check
    # -------------------------------------------------------------------------

    def commit_check(self):
        """
        Perform a commit check.  If the commit check passes, this function
        will return ``True``.  If the commit-check results in warnings, they
        are reported and available in the Exception errs.

        :returns: ``True`` if commit-check is successful (no errors)
        :raises CommitError: When errors detected in candidate configuration.
                             You can use the Exception errs variable
                             to identify the specific problems
        :raises RpcError: When underlying ncclient has an error
        """
        try:
            self.rpc.commit_configuration(check=True)
        except RpcError as err:        # jnpr.junos exception
            if err.rsp.find('ok') is not None:
                # this means there is a warning, but no errors
                return True
            else:
                raise CommitError(cmd=err.cmd, rsp=err.rsp)
        except Exception as err:
            # :err: is from ncclient, so extract the XML data
            # and convert into dictionary
            return JXML.rpc_error(err.xml)

        return True

    # -------------------------------------------------------------------------
    # show | compare rollback <number|0*>
    # -------------------------------------------------------------------------

    def diff(self, rb_id=0):
        """
        Retrieve a diff (patch-format) report of the candidate config against
        either the current active config, or a different rollback.

        :param int rollback: rollback id [0..49]

        :returns:
            * ``None`` if there is no difference
            * ascii-text (str) if there is a difference
        """

        if rb_id < 0 or rb_id > 49:
            raise ValueError("Invalid rollback #" + str(rb_id))

        rsp = self.rpc.get_configuration(dict(
            compare='rollback', rollback=str(rb_id), format='text'
        ))

        diff_txt = rsp.find('configuration-output').text
        return None if diff_txt == "\n" else diff_txt

    def pdiff(self, rb_id=0):
        """
        Helper method that calls ``print`` on the diff (patch-format) between the
        current candidate and the provided rollback.

        :param int rb_id: the rollback id value [0-49]

        :returns: ``None``
        """
        print self.diff(rb_id)

    # -------------------------------------------------------------------------
    # helper on loading configs
    # -------------------------------------------------------------------------

    def load(self, *vargs, **kvargs):
        """
        Loads changes into the candidate configuration.  Changes can be
        in the form of strings (text,set,xml), XML objects, and files.
        Files can be either static snippets of configuration or Jinja2
        templates.  When using Jinja2 Templates, this method will render
        variables into the templates and then load the resulting change;
        i.e. "template building".

        :param object vargs[0]:
            The content to load.  If the contents is a string, the framework
            will attempt to automatically determine the format.  If it is
            unable to determine the format then you must specify the
            **format** parameter.  If the content is an XML object, then
            this method assumes you've structured it correctly;
            and if not an Exception will be raised.

        :param str path:
            Path to file of configuration on the local server.
            The path extension will be used to determine the format of
            the contents:

            * "conf","text","txt" is curly-text-style
            * "set" - ascii-text, set-style
            * "xml" - ascii-text, XML

            .. note:: The format can specifically set using **format**.

        :param str format:
          Determines the format of the contents. Refer to options
          from the **path** description.

        :param bool overwrite:
          Determines if the contents completely replace the existing
          configuration.  Default is ``False``.

          .. note:: This option cannot be used if **format** is "set".

        :param bool merge:
          If set to ``True`` will set the load-config action to merge.
          the default load-config action is 'replace'

        :param str template_path:
          Similar to the **path** parameter, but this indicates that
          the file contents are ``Jinja2`` format and will require
          template-rendering.

          .. note:: This parameter is used in conjunction with **template_vars**.
                     The template filename extension will be used to determine
                     the format-style of the contents, or you can override
                     using **format**.

        :param jinja2.Template template:
          A Jinja2 Template object.  Same description as *template_path*,
          except this option you provide the actual Template, rather than
          a path to the template file.

        :param dict template_vars:
          Used in conjunction with the other template options.  This parameter
          contains a dictionary of variables to render into the template.

        :returns:
            RPC-reply as XML object.

        :raises: ConfigLoadError: When errors detected while loading candidate configuration.
                             You can use the Exception errs variable
                             to identify the specific problems
        """
        rpc_xattrs = {}
        rpc_xattrs['format'] = 'xml'        # default to XML format
        rpc_xattrs['action'] = 'replace'    # replace is default action

        rpc_contents = None

        # support the ability to completely replace the Junos configuration
        # note: this cannot be used if format='set', per Junos API.

        overwrite = kvargs.get('overwrite', False)
        if True == overwrite:
            rpc_xattrs['action'] = 'override'
        elif kvargs.get('merge') is True:
            del rpc_xattrs['action']

        # ---------------------------------------------------------------------
        # private helpers ...
        # ---------------------------------------------------------------------

        def _lformat_byext(path):
            """ determine the format style from the file extension """
            ext = os.path.splitext(path)[1]
            if ext == '.xml':
                return 'xml'
            if ext in ['.conf', '.text', '.txt']:
                return 'text'
            if ext in ['.set']:
                return 'set'
            raise ValueError("Unknown file contents from extension: %s" % ext)

        def _lset_format(kvargs, rpc_xattrs):
            """ setup the kvargs/rpc_xattrs """
            # when format is given, setup the xml attrs appropriately
            if kvargs['format'] == 'set':
                if True == overwrite:
                    raise ValueError(
                        "conflicting args, cannot use 'set' with 'overwrite'")
                rpc_xattrs['action'] = 'set'
                kvargs['format'] = 'text'
            rpc_xattrs['format'] = kvargs['format']

        def _lset_fromfile(path):
            """ setup the kvargs/rpc_xattrs based on path """
            if 'format' not in kvargs:
                # we use the extension to determine the format
                kvargs['format'] = _lformat_byext(path)
                _lset_format(kvargs, rpc_xattrs)

        def _lset_from_rexp(rpc):
            """ setup the kvargs/rpc_xattrs using string regular expression """
            if re.search(r'^\s*<.*>$', rpc, re.MULTILINE):
                kvargs['format'] = 'xml'
            elif re.search(r'^\s*(set|delete|replace|rename)\s', rpc):
                kvargs['format'] = 'set'
            elif re.search(r'^[a-z:]*\s*\w+\s+{', rpc, re.I) and re.search(r'.*}\s*$', rpc):
                kvargs['format'] = 'text'

        def try_load(rpc_contents, rpc_xattrs):
            try:
                got = self.rpc.load_config(rpc_contents, **rpc_xattrs)
            except RpcError as err:
                raise ConfigLoadError(cmd=err.cmd, rsp=err.rsp, errs=err.errs)
            # Something unexpected happened - raise it up
            except Exception as err:
                raise

            return got

        # ---------------------------------------------------------------------
        # end-of: private helpers
        # ---------------------------------------------------------------------

        if 'format' in kvargs:
            _lset_format(kvargs, rpc_xattrs)

        # ---------------------------------------------------------------------
        # if contents are provided as vargs[0], then process that as XML or str
        # ---------------------------------------------------------------------

        if len(vargs):
            # caller is providing the content directly.
            rpc_contents = vargs[0]
            if isinstance(rpc_contents, str):
                if 'format' not in kvargs:
                    _lset_from_rexp(rpc_contents)
                    if 'format' in kvargs:
                        _lset_format(kvargs, rpc_xattrs)
                    else:
                        raise RuntimeError(
                            "Not able to resolve the config format "
                            "You must define the format of the contents explicitly "
                            "to the function. Ex: format='set'")
                if kvargs['format'] == 'xml':
                # covert the XML string into XML structure
                    rpc_contents = etree.XML(rpc_contents)
            return try_load(rpc_contents, rpc_xattrs)
            # ~! UNREACHABLE !~#

        # ---------------------------------------------------------------------
        # if path is provided, use the static-config file
        # ---------------------------------------------------------------------

        if 'path' in kvargs:
            # then this is a static-config file.  load that as our rpc_contents
            rpc_contents = open(kvargs['path'], 'rU').read()
            _lset_fromfile(kvargs['path'])
            if rpc_xattrs['format'] == 'xml':
                # covert the XML string into XML structure
                rpc_contents = etree.XML(rpc_contents)

            return try_load(rpc_contents, rpc_xattrs)

            # ~! UNREACHABLE !~#

        # ---------------------------------------------------------------------
        # if template_path is provided, then jinja2 load the template, and
        # render the results.  if template_vars are provided, use those
        # in the render process.
        # ---------------------------------------------------------------------

        if 'template_path' in kvargs:
            path = kvargs['template_path']
            template = self.dev.Template(path)
            rpc_contents = template.render(kvargs.get('template_vars', {}))
            _lset_fromfile(path)
            if rpc_xattrs['format'] == 'xml':
                # covert the XML string into XML structure
                rpc_contents = etree.XML(rpc_contents)

            return try_load(rpc_contents, rpc_xattrs)

            # ~! UNREACHABLE !~#

        # ---------------------------------------------------------------------
        # if template is provided, then this is a pre-loaded jinja2 Template
        # object.  Use the template.filename to determine the format style
        # ---------------------------------------------------------------------

        if 'template' in kvargs:
            template = kvargs['template']
            path = template.filename
            rpc_contents = template.render(kvargs.get('template_vars', {}))
            _lset_fromfile(path)
            if rpc_xattrs['format'] == 'xml':
                # covert the XML string into XML structure
                rpc_contents = etree.XML(rpc_contents)

            return try_load(rpc_contents, rpc_xattrs)

            # ~! UNREACHABLE !~#

        raise RuntimeError("Unhandled load request")

    # -------------------------------------------------------------------------
    # config exclusive
    # -------------------------------------------------------------------------

    def lock(self):
        """
        Attempts an exclusive lock on the candidate configuration.  This
        is a non-blocking call.

        :returns:
            ``True`` always when successful

        :raises LockError: When the lock cannot be obtained
        """
        try:
            self.rpc.lock_configuration()
        except Exception as err:
            if isinstance(err, RpcError):
                raise LockError(rsp=err.rsp)
            else:
                # :err: is from ncclient
                raise LockError(rsp=JXML.remove_namespaces(err.xml))

        return True

    # -------------------------------------------------------------------------
    # releases the exclusive lock
    # -------------------------------------------------------------------------

    def unlock(self):
        """
        Unlocks the candidate configuration.

        :returns:
            ``True`` always when successful

        :raises UnlockError: If you attempt to unlock a configuration
                             when you do not own the lock
        """
        try:
            self.rpc.unlock_configuration()
        except Exception as err:
            if isinstance(err, RpcError):
                raise UnlockError(rsp=err.rsp)
            else:
                # :err: is from ncclient
                raise UnlockError(rsp=JXML.remove_namespaces(err.xml))

        return True

    # -------------------------------------------------------------------------
    # rollback <number|0*>
    # -------------------------------------------------------------------------

    def rollback(self, rb_id=0):
        """
        Rollback the candidate config to either the last active or
        a specific rollback number.

        :param str rb_id: The rollback id value [0-49], defaults to ``0``.

        :returns:
            ``True`` always when successful

        :raises ValueError: When invalid rollback id is given
        """

        if rb_id < 0 or rb_id > 49:
            raise ValueError("Invalid rollback #" + str(rb_id))

        self.rpc.load_configuration(dict(
            compare='rollback', rollback=str(rb_id)
        ))

        return True

    # -------------------------------------------------------------------------
    # rescue configuration
    # -------------------------------------------------------------------------

    def rescue(self, action, format='text'):
        """
        Perform action on the "rescue configuration".

        :param str action: identifes the action as follows:

            * "get" - retrieves/returns the rescue configuration via **format**
            * "save" - saves current configuration as rescue
            * "delete" - removes the rescue configuration
            * "reload" - loads the rescue config as candidate (no-commit)

        :param str format: identifies the return format when **action** is "get":

            * "text" (default) - ascii-text format
            * "xml" - as XML object

        :return:

            * When **action** is 'get', then the contents of the rescue configuration
              is returned in the specified *format*.  If there is no rescue configuration
              saved, then the return value is ``None``.

            * ``True`` when **action** is "save".

            * ``True`` when **action** is "delete".

            .. note:: ``True`` regardless if a rescue configuration exists.

            * When **action** is 'reload', return is ``True`` if a rescue configuration
              exists, and ``False`` otherwise.

            .. note:: The rescue configuration is only loaded as the candidate,
                      and not committed.  You must commit to make the rescue
                      configuration active.

        :raises ValueError:
            If **action** is not one of the above
        """

        def _rescue_save():
            """
            Saves the current configuration as the rescue configuration
            """
            self.rpc.request_save_rescue_configuration()
            return True

        def _rescue_delete():
            """
            Deletes the existing resuce configuration.
            """
            # note that this will result in an "OK" regardless if
            # a rescue config exists or not.
            self.rpc.request_delete_rescue_configuration()
            return True

        def _rescue_get():
            """
            Retrieves the rescue configuration, returning it in
            either :format: 'text' or 'xml'.

            Returns either the 'text'/'xml' if the rescue config
            exists, or :None: otherwise
            """
            try:
                got = self.rpc.get_rescue_information(format=format)
                return got.findtext('configuration-information/configuration-output') \
                    if 'text' == format else got
            except:
                return None

        def _rescue_reload():
            """
            Loads the rescue configuration as the active candidate.
            This action does *not* commit the configuration; use the
            :commit(): method for that purpose.

            Returns the XML response if the rescue configuration
            exists, or :False: otherwise
            """
            try:
                return self.rpc.load_configuration({'rescue': 'rescue'})
            except:
                return False

        def _unsupported_action():
            raise ValueError("unsupported action: {0}".format(action))

        result = {
            'get': _rescue_get,
            'save': _rescue_save,
            'delete': _rescue_delete,
            'reload': _rescue_reload
        }.get(action, _unsupported_action)()

        return result
