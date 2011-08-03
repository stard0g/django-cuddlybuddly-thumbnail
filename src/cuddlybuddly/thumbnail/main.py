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
from django.db.models.fields.files import FieldFile
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

CUDDLYBUDDLY_NOIMAGE_IMAGE = getattr(settings, 'CUDDLYBUDDLY_NOIMAGE_IMAGE', 'no-image.jpg')

def build_thumbnail_name(source_image, width, height, processor):
    source_image = force_unicode(source_image)
    url = urlparse(source_image)
    path, filename = os.path.split(source_image)

    if url.scheme != "":
        return (True, os.path.join(
            settings.MEDIA_ROOT,
            getattr(settings, 'CUDDLYBUDDLY_REMOTE_BASEDIR', ''),
            filename
        ))
    else:
        filename = processor.generate_filename(filename, width, height)
        return (False, os.path.join(
            getattr(settings, 'CUDDLYBUDDLY_THUMBNAIL_BASEDIR', ''),
            path.replace(settings.MEDIA_URL, ''),
            getattr(settings, 'CUDDLYBUDDLY_THUMBNAIL_SUBDIR', ''),
            filename
        ))


class Thumbnail(object):
    def __init__(self, source_image, width, height, dest=None, proc=None, *args,
                 **kwargs):
        self.source_image = source_image
        self.width = width
        self.height = height
        self.processor = get_processor(proc)(*args, **kwargs)

        is_remote = False
        if dest is None:
            is_remote, dest = build_thumbnail_name(source_image, width, height, self.processor)

        self.is_remote = is_remote
        self.dest = dest

        # Is a remote image download the image and make available
        if self.is_remote:
            source_image = self.get_remote_image(self.source_image, self.dest)
            self.source_image = source_image
            self.is_remote, self.dest = build_thumbnail_name(source_image, width, height, self.processor)

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
                if isinstance(self.source_image, FieldFile) or \
                   isinstance(self.source_image, File):
                    source_image = force_unicode(self.source_image)
                elif not isinstance(self.source_image, basestring):
                    source_image = pickle.dumps(self.source_image.read())
                    self.source_image.seek(0)
                else:
                    source_image = smart_str(force_unicode(self.source_image))

                source_image = os.path.join(self.cache_dir,
                                      md5_constructor(source_image).hexdigest())
                if not os.path.exists(source_image):
                    path = os.path.split(source_image)[0]
                    if not os.path.exists(path):
                        os.makedirs(path)
                    open(source_image, 'w').close()
                if not isinstance(self.dest, basestring):
                    dest = pickle.dumps(self.dest.read())
                    self.dest.seek(0)
                else:
                    dest = smart_str(force_unicode(self.dest))
                dest = os.path.join(self.cache_dir,
                                      md5_constructor(dest).hexdigest())
            else:
                source_image = force_unicode(self.source_image)
                dest = self.dest

            # If the destination file does not exist then generate it
            if not os.path.exists(dest):
                do_generate = True
            else:
                # otherwise do this hodge podge of time comparisons
                if hasattr(default_storage, 'modified_time') and not self.cache_dir:

                    do_generate = default_storage.modified_time(source_image) > \
                            default_storage.modified_time(dest)

                elif hasattr(default_storage, 'getmtime') and not self.cache_dir:
                    # An old custom method from before Django supported
                    # modified_time(). Kept around for backwards compatibility.
                    do_generate = default_storage.getmtime(source_image) > \
                            default_storage.getmtime(dest)
                else:
                    if not self.cache_dir:
                        source_image_cache = os.path.join(settings.MEDIA_ROOT, source_image)
                        dest_cache = os.path.join(settings.MEDIA_ROOT, dest)
                    else:
                        source_image_cache, dest_cache = source_image, dest
                    try:
                        do_generate = os.path.getmtime(source_image_cache) > \
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

    def get_remote_image(self, url, dest):
        """ build the unique filename and then check if it has been created already
        if not then download it from the remote url and save it locally
        """
        download_path, filename = os.path.split(dest)

        # make unique filename
        filename = self.get_remote_filename(url)

        file_name = '%s/%s' % (download_path, filename)

        if not default_storage.exists(file_name):
            try:
                urllib.urlretrieve( url, file_name )
                Image.open(file_name)
            except Exception, e:
                logger.error('Could not download: %s and store at: %s' % (url, file_name))
                file_name = CUDDLYBUDDLY_NOIMAGE_IMAGE

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
        if isinstance(self.source_image, Image.Image):
            data = self.source_image
        else:
            try:
                if not hasattr(self.source_image, 'readline'):
                    if not hasattr(self.source_image, 'read'):
                        source_image = force_unicode(self.source_image)
                        try:
                            file = default_storage.open(source_image, 'rb')
                        except:
                            file = default_storage.open(CUDDLYBUDDLY_NOIMAGE_IMAGE, 'rb')

                        content = ContentFile(file.read())
                        file.close()
                    else:
                        content = ContentFile(self.source_image.read())
                else:
                    content = ContentFile(self.source_image.read())
                data = Image.open(content)
            except IOError, detail:
                raise ThumbnailException('%s: %s' % (detail, self.source_image))
            except MemoryError:
                raise ThumbnailException('Memory Error: %s' % self.source_image)

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

        if hasattr(self.source_image, 'seek'):
            self.source_image.seek(0)
        if filelike:
            dest.seek(0)
        else:
            if default_storage.exists(filename):
                default_storage.delete(filename)
            default_storage.save(filename, ContentFile(dest.getvalue()))
            dest.close()
