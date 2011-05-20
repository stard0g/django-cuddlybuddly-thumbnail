import os
import pickle
try:
    from PIL import Image
except ImportError:
    import Image
try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO
from django.conf import settings
from django.core.files.base import ContentFile, File
from django.core.files.storage import default_storage
from django.db.models.fields.files import FieldFile, ImageFile
from django.utils.encoding import force_unicode, smart_str
from django.utils.hashcompat import md5_constructor
from django.template.defaultfilters import slugify
from cuddlybuddly.thumbnail import get_processor
from cuddlybuddly.thumbnail.exceptions import ThumbnailException

from PIL import Image
from urlparse import urlparse
import urllib, urllib2

import logging
logger = logging.getLogger(__name__)


def build_thumbnail_name(source, width, height, processor):
    source = force_unicode(source)
    path, filename = os.path.split(source)
    filename = processor.generate_filename(filename, width, height)
    return os.path.join(
        getattr(settings, 'CUDDLYBUDDLY_THUMBNAIL_BASEDIR', ''),
        path,
        getattr(settings, 'CUDDLYBUDDLY_THUMBNAIL_SUBDIR', ''),
        filename
    )


class Thumbnail(object):
    def __init__(self, source, width, height, dest=None, proc=None, *args,
                 **kwargs):
        self.source = source
        self.width = width
        self.height = height
        self.processor = get_processor(proc)(*args, **kwargs)
        if dest is None:
            dest = build_thumbnail_name(source, width, height, self.processor)
        self.dest = dest
        self.cache_dir = getattr(settings, 'CUDDLYBUDDLY_THUMBNAIL_CACHE', None)

        for var in ('width', 'height'):
            try:
                setattr(self, var, int(getattr(self, var)))
            except ValueError:
                raise ThumbnailException('Value supplied for \'%s\' is not an int' % var)
        if self.processor is None:
            raise ThumbnailException('There is no image processor available')

        self.generate()

    def __unicode__(self):
        return force_unicode(self.dest)

    def generate(self):
        if hasattr(self.dest, 'write'):
            self._do_generate()
        else:
            do_generate = False
            if self.cache_dir is not None:
                if isinstance(self.source, FieldFile) or \
                   isinstance(self.source, File):
                    source = force_unicode(self.source)
                elif not isinstance(self.source, basestring):
                    source = pickle.dumps(self.source.read())
                    self.source.seek(0)
                else:
                    source = smart_str(force_unicode(self.source))

                source = os.path.join(self.cache_dir,
                                      md5_constructor(source).hexdigest())
                if not os.path.exists(source):
                    path = os.path.split(source)[0]
                    if not os.path.exists(path):
                        os.makedirs(path)
                    open(source, 'w').close()
                if not isinstance(self.dest, basestring):
                    dest = pickle.dumps(self.dest.read())
                    self.dest.seek(0)
                else:
                    dest = smart_str(force_unicode(self.dest))
                dest = os.path.join(self.cache_dir,
                                      md5_constructor(dest).hexdigest())
            else:
                source = force_unicode(self.source)
                dest = self.dest

            if hasattr(default_storage, 'modified_time') and not self.cache_dir:
                do_generate = default_storage.modified_time(source) > \
                        default_storage.modified_time(dest)
            elif hasattr(default_storage, 'getmtime') and not self.cache_dir:
                # An old custom method from before Django supported
                # modified_time(). Kept around for backwards compatibility.
                do_generate = default_storage.getmtime(source) > \
                        default_storage.getmtime(dest)
            else:
                if not self.cache_dir:
                    source_cache = os.path.join(settings.MEDIA_ROOT, source)
                    dest_cache = os.path.join(settings.MEDIA_ROOT, dest)
                else:
                    source_cache, dest_cache = source, dest
                try:
                    do_generate = os.path.getmtime(source_cache) > \
                            os.path.getmtime(dest_cache)
                except OSError:
                    do_generate = True

            if do_generate:
                if self.cache_dir is not None:
                    path = os.path.split(dest)[0]
                    if not os.path.exists(path):
                        os.makedirs(path)
                    open(dest, 'w').close()
                try:
                    self._do_generate()
                except:
                    if self.cache_dir is not None:
                        if os.path.exists(dest):
                            os.remove(dest)
                    raise

    def get_remote_image(self, url):
        """ build the unique filename and then check if it has been created already
        if not then download it from the remote url and save it locally
        """
        filename = self.get_remote_filename(url)
        self.dest = build_thumbnail_name(filename, self.width, self.height, self.processor)

        file_name = '%s_remote/%s' % (settings.MEDIA_ROOT, filename)

        if not default_storage.exists(file_name):
            try:
                urllib.urlretrieve( url, file_name )
            except Exception, e:
                raise ThumbnailException('Could not download: %s and store at: %s'
                                         % (url, file_name))

        logger.debug('returning remote_get file_name: %s' % (file_name,))
        return force_unicode(file_name)

    def get_remote_filename(self, url):
        """ Convert the remote filename into a local unique name
        md5_ the whole url and prefix it to the remote filename
        """
        logger.debug('get remote_filename')
        url = str(url)
        filename = url.split('/')
        return '%s-%s' % (md5_constructor(url).hexdigest(), str(filename[-1]))

    def _do_generate(self):
        if isinstance(self.source, Image.Image):
            data = self.source
        else:
            try:
                if not hasattr(self.source, 'readline'):
                    if not hasattr(self.source, 'read'):
                        source = force_unicode(self.source)
                        if not default_storage.exists(source):
                            # test source for being a url
                            url = urlparse(source)
                            if url.scheme is not None:
                                source = self.get_remote_image(source)
                            else:
                                raise ThumbnailException('Source does not exist: %s'
                                                         % self.source)
                        file = default_storage.open(source, 'rb')
                        content = ContentFile(file.read())
                        file.close()
                    else:
                        content = ContentFile(self.source.read())
                else:
                    content = ContentFile(self.source.read())
                data = Image.open(content)
            except IOError, detail:
                raise ThumbnailException('%s: %s' % (detail, self.source))
            except MemoryError:
                raise ThumbnailException('Memory Error: %s' % self.source)

        filelike = hasattr(self.dest, 'write')
        if not filelike:
            dest = StringIO()
        else:
            dest = self.dest

        data = self.processor.generate_thumbnail(data, self.width, self.height)

        filename = force_unicode(self.dest)
        try:
            data.save(dest, optimize=1, **self.processor.get_save_options(filename, data))
        except IOError:
            # Try again, without optimization (PIL can't optimize an image
            # larger than ImageFile.MAXBLOCK, which is 64k by default)
            try:
                data.save(dest, **self.processor.get_save_options(filename, data))
            except IOError, e:
                raise ThumbnailException(e)

        if hasattr(self.source, 'seek'):
            self.source.seek(0)
        if filelike:
            dest.seek(0)
        else:
            if default_storage.exists(filename):
                default_storage.delete(filename)
            default_storage.save(filename, ContentFile(dest.getvalue()))
            dest.close()
