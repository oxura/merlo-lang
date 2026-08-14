#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef struct Node { uint64_t value; struct Node *left, *right; } Node;
static Node *build(uint64_t value, int depth) {
    if (depth == 0) return NULL;
    Node *node = malloc(sizeof(Node)); if (!node) abort();
    node->value=value; node->left=build(value*2,depth-1); node->right=build(value*2+1,depth-1); return node;
}
static uint64_t fold(const Node *node) { return node ? node->value + fold(node->left) + fold(node->right) : 0; }
static void destroy(Node *node) { if(node){destroy(node->left);destroy(node->right);free(node);} }
static uint64_t run(uint64_t n) { Node *root=build(1,12); uint64_t sum=0; for(uint64_t i=0;i<n;++i)sum+=fold(root); destroy(root); return sum; }
int main(int argc,char **argv){if(argc!=2)return 2;uint64_t n=strtoull(argv[1],NULL,10);printf("%" PRIu64 "\n",run(n));return 0;}
