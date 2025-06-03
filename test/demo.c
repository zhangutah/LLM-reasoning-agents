#include <stdbool.h>
#include "dns/types.h"


#define UTEST_LYCTX (_UC->ctx)
typedef void  (*Dwarf_Handler)(Dwarf_Error dw_error, Dwarf_Ptr dw_errarg);
typedef struct Dwarf_Error_s*      Dwarf_Error;
typedef struct Dwarf_CU_Context_s  *Dwarf_CU_Context;
typedef struct _GdkPixbufAnimation GdkPixbufAnimation;

static inline const char *JS_ToCString(JSContext *ctx, JSValueConst val1)
{
    return JS_ToCStringLen2(ctx, NULL, val1, 0);
}


typedef struct _Str {
    char *ptr;
    int length;
    int area_size;
} Str;



/** STUN decoding options */
enum pj_stun_decode_options
{
    /** 
     * Tell the decoder that the message was received from datagram
     * oriented transport (such as UDP).
     */
    PJ_STUN_IS_DATAGRAM     = 1,
};

sip_msg_t tmsg;
int faked_msg_match(sip_msg_t tmsg);
struct hdr_field *diversion;
typedef char isc_lexspecials_t[256];
extern const dns_name_t dns_rootname;
struct dns_message {
        /* public from here down */
        unsigned int   magic;
        isc_refcount_t references;

        dns_messageid_t  id;
        unsigned int     flags;
        dns_rcode_t      rcode;
        dns_opcode_t     opcode;
        dns_rdataclass_t rdclass;

};

#define isc_mem_destroy(cp) isc__mem_destroy((cp)_ISC_MEM_FILELINE)

extern unsigned int isc_mem_debugging;


#define ISC_LEXCOMMENT_DNSMASTERFILE 0x08

enum dns_decompress {
        DNS_DECOMPRESS_DEFAULT,
        DNS_DECOMPRESS_PERMITTED,
        DNS_DECOMPRESS_NEVER,
        DNS_DECOMPRESS_ALWAYS,
};

enum {
        dns_rdataclass_reserved0 = 0,
#define dns_rdataclass_reserved0 \
                                ((dns_rdataclass_t)dns_rdataclass_reserved0)
        dns_rdataclass_in = 1,
#define dns_rdataclass_in       ((dns_rdataclass_t)dns_rdataclass_in)
        dns_rdataclass_chaos = 3,
#define dns_rdataclass_chaos    ((dns_rdataclass_t)dns_rdataclass_chaos)
        dns_rdataclass_ch = 3,
#define dns_rdataclass_ch       ((dns_rdataclass_t)dns_rdataclass_ch)
        dns_rdataclass_hs = 4,
#define dns_rdataclass_hs       ((dns_rdataclass_t)dns_rdataclass_hs)
        dns_rdataclass_none = 254,
#define dns_rdataclass_none     ((dns_rdataclass_t)dns_rdataclass_none)
        dns_rdataclass_any = 255
#define dns_rdataclass_any      ((dns_rdataclass_t)dns_rdataclass_any)
};


static inline dns_decompress_t /* inline to suppress code generation */
dns_decompress_setpermitted(dns_decompress_t dctx, bool permitted) {
        if (dctx == DNS_DECOMPRESS_NEVER || dctx == DNS_DECOMPRESS_ALWAYS) {
                return dctx;
        } else if (permitted) {
                return DNS_DECOMPRESS_PERMITTED;
        } else {
                return DNS_DECOMPRESS_DEFAULT;
        }
}

IGRAPH_EXPORT igraph_error_t igraph_read_graph_pajek(igraph_t *graph, FILE *instream){

}
LLAMA_API int32_t llama_vocab_n_tokens(const struct llama_vocab * vocab);
LIBBPF_API struct bpf_object *
bpf_object__open_mem(const void *obj_buf, size_t obj_buf_sz,
                     const struct bpf_object_open_opts *opts);


LLAMA_API uint32_t llama_model_quantize(
    const char * fname_inp,
    const char * fname_out,
    const llama_model_quantize_params * params);
            

int
LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
        isc_buffer_t buf;
        isc_result_t result;

        isc_buffer_constinit(&buf, data, size);
        isc_buffer_add(&buf, size);
        isc_buffer_setactive(&buf, size);

        CHECK(isc_lex_openbuffer(lex, &buf));

        do {
                isc_token_t token;
                result = isc_lex_gettoken(lex, 0, &token);
        } while (result == ISC_R_SUCCESS);

        return 0;
}


// struct_specifier_query
struct dns_zone {
	/* Unlocked */
	unsigned int magic;
	isc_mutex_t lock;
#ifdef DNS_ZONE_CHECKLOCK
	bool locked;
#endif /* ifdef DNS_ZONE_CHECKLOCK */
	isc_mem_t *mctx;
	isc_refcount_t references;

	isc_rwlock_t dblock;
	dns_db_t *db; /* Locked by dblock */

	unsigned int tid;
        dns_zone_t *master;       
	/* Locked */
	dns_zonemgr_t *zmgr;
	ISC_LINK(dns_zone_t) link; /* Used by zmgr. */
	isc_loop_t *loop;
}

// struct_specifier_query
typedef struct A {
        bool is_valid;
} B;

// type definnition
typedef struct dns_name	 dns_name_t;
typedef unsigned int uint32_t;

// template 
template <typename T>
T myMax(T x, T y) {
  return (x > y) ? x : y;
}


#pragma once
enum {
        dns_rdatatype_none = 0,
        dns_rdatatype_a = 1,
        dns_rdatatype_ns = 2,
        dns_rdatatype_md = 3,
        dns_rdatatype_mf = 4,
};

dns_name_t *
dns_fixedname_initname(dns_fixedname_t *fixed);
